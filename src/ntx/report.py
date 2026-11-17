"""Evidence brief rendering (Markdown + self-contained HTML)."""

from __future__ import annotations

import html
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import clock, reconcile
from .store import Store

BAND_ORDER = {"high": 3, "moderate": 2, "low": 1}
DISCLAIMER = (
    "Evidence confidence is a triage signal produced by a transparent rubric. "
    "It is not the probability that a claim is true, and absence of a retraction "
    "notice is not proof that a study is valid."
)


def _rows(store: Store, entity_id: str) -> list[dict[str, Any]]:
    cur = store.conn.execute(
        "SELECT c.*, d.title, d.journal, d.year, d.pmid, d.doi, d.retraction_status,"
        " d.pubtypes, a.band, a.rubric_points, a.bias_flags, a.reasons"
        " FROM claims c JOIN documents d ON d.doc_id = c.doc_id"
        " LEFT JOIN appraisals a ON a.doc_id = c.doc_id AND a.entity_id = c.entity_id"
        " AND a.start = c.start AND a.end = c.end"
        " WHERE c.entity_id = ?",
        (entity_id,),
    )
    return [dict(row) for row in cur]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    designs = Counter(r["design"] or "unknown" for r in rows)
    bands = Counter(r["band"] or "low" for r in rows)
    directions: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        directions[row["outcome"] or "other"][row["direction"] or "unclear"] += 1
    contradictions = []
    for outcome, counter in directions.items():
        if counter["increase"] and counter["decrease"]:
            contradictions.append(outcome)
    interventions: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if (row["role"] or "intervention") == "intervention":
            interventions[row["outcome"] or "other"][row["direction"] or "unclear"] += 1
    contradictions = sorted(
        outcome for outcome, counter in interventions.items() if counter["increase"] and counter["decrease"]
    )
    retractions = [r for r in rows if r["retraction_status"] in {"retracted", "concern", "retraction_notice"}]
    docs = {r["doc_id"] for r in rows}
    return {
        "claims": len(rows),
        "documents": len(docs),
        "designs": dict(designs),
        "bands": dict(bands),
        "directions": {k: dict(v) for k, v in directions.items()},
        "contradictions": sorted(contradictions),
        "retractions": retractions,
    }


def _quote(row: dict[str, Any]) -> str:
    text = (row.get("sentence") or "").strip()
    if len(text) > 320:
        text = text[:317] + "..."
    return text


def _evidence(row: dict[str, Any]) -> str:
    """Return the clause that carries the claim, relative to the sentence start."""
    sentence = row.get("sentence") or ""
    start = row.get("evidence_start") or 0
    end = row.get("evidence_end") or 0
    claim_start = row.get("start") or 0
    if start and end and claim_start <= start < end <= claim_start + len(sentence):
        clause = sentence[start - claim_start : end - claim_start].strip()
        if len(clause) >= 15:
            return clause
    return sentence.strip()


def _link(row: dict[str, Any]) -> str:
    if row.get("pmid"):
        return "https://pubmed.ncbi.nlm.nih.gov/%s/" % row["pmid"]
    if row.get("doi"):
        return "https://doi.org/" + row["doi"]
    return ""


def _reconciliation_markdown(reconciliation: dict[str, Any]) -> list[str]:
    lines = ["", "## Full-text reconciliation", ""]
    documents = reconciliation.get("documents", 0)
    if not documents:
        lines.append("No reconcilable abstract/full-text groups were available for this entity.")
        return lines
    counts = reconciliation["counts"]
    severity = reconciliation["severity_counts"]
    lines.append("- Documents compared: **%d**" % documents)
    lines.append(
        "- Status: corroborated=%d, direction_conflict=%d, abstract_only=%d, "
        "fulltext_only=%d, indeterminate=%d"
        % (
            counts.get("corroborated", 0),
            counts.get("direction_conflict", 0),
            counts.get("abstract_only", 0),
            counts.get("fulltext_only", 0),
            counts.get("indeterminate", 0),
        )
    )
    lines.append(
        "- Findings: high=%d, medium=%d, low=%d"
        % (severity.get("high", 0), severity.get("medium", 0), severity.get("low", 0))
    )
    lines.extend(["", "| Status | Count |", "| --- | ---: |"])
    for status in reconcile.STATUSES:
        lines.append("| %s | %d |" % (status, counts.get(status, 0)))
    findings = reconciliation.get("findings", [])
    if findings:
        lines.extend(
            [
                "",
                "| Outcome | Status | Severity | Abstract | Full text |",
                "| --- | --- | --- | ---: | ---: |",
            ]
        )
        for finding in findings[:10]:
            lines.append(
                "| %s | %s | %s | %d | %d |"
                % (
                    finding["outcome"],
                    finding["status"],
                    finding["severity"],
                    finding["abstract_count"],
                    finding["fulltext_count"],
                )
            )
        lines.extend(["", "### Reconciliation findings", ""])
        for finding in findings[:10]:
            lines.append(
                "- **%s** / %s: `%s` (%s)"
                % (
                    finding["entity_id"],
                    finding["outcome"],
                    finding["status"],
                    finding["severity"],
                )
            )
            if finding["reasons"]:
                lines.append("  - reasons: " + ", ".join(finding["reasons"]))
            quote = (finding.get("quote") or {}).get("text") or ""
            if quote:
                lines.append("  > " + quote.replace("\n", " "))
            link = _link(finding)
            if link:
                lines.append("  <%s>" % link)
    lines.extend(
        [
            "",
            "Reconciliation uses PMC open-access JATS only. `corroborated` means the stored "
            "layers contain a matching direction; it is not proof that the abstract is correct.",
        ]
    )
    return lines


def _reconciliation_html(reconciliation: dict[str, Any]) -> list[str]:
    parts = ["<h2>Full-text reconciliation</h2>"]
    documents = reconciliation.get("documents", 0)
    if not documents:
        parts.append(
            "<p class='meta'>No reconcilable abstract/full-text groups were available for this entity.</p>"
        )
        return parts
    counts = reconciliation["counts"]
    severity = reconciliation["severity_counts"]
    parts.append(
        "<p>Compared <strong>%d</strong> document(s): corroborated=%d, direction conflict=%d, "
        "abstract only=%d, full-text only=%d, indeterminate=%d. "
        "Findings: high=%d, medium=%d, low=%d.</p>"
        % (
            documents,
            counts.get("corroborated", 0),
            counts.get("direction_conflict", 0),
            counts.get("abstract_only", 0),
            counts.get("fulltext_only", 0),
            counts.get("indeterminate", 0),
            severity.get("high", 0),
            severity.get("medium", 0),
            severity.get("low", 0),
        )
    )
    findings = reconciliation.get("findings", [])
    if findings:
        parts.append(
            "<table><tr><th>Outcome</th><th>Status</th><th>Severity</th>"
            "<th>Abstract</th><th>Full text</th></tr>"
        )
        for finding in findings[:10]:
            parts.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td></tr>"
                % (
                    html.escape(finding["outcome"]),
                    html.escape(finding["status"]),
                    html.escape(finding["severity"]),
                    finding["abstract_count"],
                    finding["fulltext_count"],
                )
            )
        parts.append("</table>")
        parts.append("<h3>Reconciliation findings</h3>")
        for finding in findings[:10]:
            link = _link(finding)
            title = "%s / %s" % (finding["entity_id"], finding["outcome"])
            if link:
                title = "<a href='%s'>%s</a>" % (html.escape(link), html.escape(title))
            parts.append(
                "<p><strong>%s</strong> <span class='badge'>%s</span> "
                "<span class='badge'>%s</span></p>"
                % (
                    title,
                    html.escape(finding["status"]),
                    html.escape(finding["severity"]),
                )
            )
            if finding["reasons"]:
                parts.append(
                    "<div class='meta'>reasons: %s</div>" % html.escape(", ".join(finding["reasons"]))
                )
            quote = (finding.get("quote") or {}).get("text") or ""
            if quote:
                parts.append("<blockquote>%s</blockquote>" % html.escape(quote))
    parts.append(
        "<p class='meta'>Reconciliation uses PMC open-access JATS only. `corroborated` means "
        "the stored layers contain a matching direction; it is not proof that the abstract is correct.</p>"
    )
    return parts


def render_markdown(
    entity_name: str,
    entity_id: str,
    rows: list[dict[str, Any]],
    reconciliation: dict[str, Any] | None = None,
) -> str:
    summary = summarize(rows)
    lines: list[str] = []
    lines.append("# Evidence brief: %s (`%s`)" % (entity_name, entity_id))
    lines.append("")
    lines.append("Generated: %s UTC" % clock.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("")
    lines.append("## Snapshot")
    lines.append("")
    lines.append("- Claims: **%d** across **%d** documents" % (summary["claims"], summary["documents"]))
    lines.append("- Designs: " + ", ".join("%s=%d" % kv for kv in sorted(summary["designs"].items())))
    lines.append("- Confidence bands: " + ", ".join("%s=%d" % kv for kv in sorted(summary["bands"].items())))
    if summary["contradictions"]:
        lines.append("- **Contradictory directions** for: " + ", ".join(summary["contradictions"]))
    else:
        lines.append("- No contradictory directions detected for the same outcome.")
    if summary["retractions"]:
        lines.append(
            "- **Integrity alerts:** %d claim(s) from retracted/flagged records" % len(summary["retractions"])
        )
    lines.append("")
    lines.append("## Outcome table")
    lines.append("")
    lines.append("| Outcome | increase | decrease | none | mixed |")
    lines.append("| --- | --- | --- | --- | --- |")
    for outcome in sorted(summary["directions"]):
        d = summary["directions"][outcome]
        lines.append(
            "| %s | %d | %d | %d | %d |"
            % (outcome, d.get("increase", 0), d.get("decrease", 0), d.get("none", 0), d.get("mixed", 0))
        )
    lines.append("")
    if summary["retractions"]:
        lines.append("## Integrity alerts")
        lines.append("")
        for row in summary["retractions"]:
            lines.append("- `%s` (%s) — %s" % (row["doc_id"], row["retraction_status"], row["title"]))
        lines.append("")
    lines.append("## Highest-confidence claims")
    lines.append("")
    ordered = sorted(
        rows,
        key=lambda r: (BAND_ORDER.get(r["band"] or "low", 0), r["rubric_points"] or 0, r["confidence"] or 0),
        reverse=True,
    )
    for row in ordered[:10]:
        lines.append(
            "- **%s** [role=%s] → %s / %s (%s, %s, band=%s)"
            % (
                row["entity_id"],
                row["role"] or "intervention",
                row["direction"],
                row["outcome"],
                row["species"],
                row["design"],
                row["band"],
            )
        )
        lines.append("  > " + _evidence(row).replace("\n", " "))
        link = _link(row)
        if link:
            lines.append("  <%s>" % link)
    if reconciliation is not None:
        lines.extend(_reconciliation_markdown(reconciliation))
    lines.append("")
    lines.append("## Method and limitations")
    lines.append("")
    if reconciliation is None:
        lines.append(
            "- Extraction is rule-based on title/abstract text; it does not read full methods, "
            "so randomization, blinding, and preregistration are under-detected."
        )
    else:
        lines.append(
            "- Claims are extracted from title/abstract; the optional reconciliation compares them "
            "with PMC open-access JATS. It does not read the publisher PDF, tables or figures."
        )
    lines.append("- Directions are sentence-level and do not resolve dose, route, or comparator effects.")
    lines.append("- " + DISCLAIMER)
    lines.append("")
    return "\n".join(lines)


def render_html(
    entity_name: str,
    entity_id: str,
    rows: list[dict[str, Any]],
    reconciliation: dict[str, Any] | None = None,
) -> str:
    summary = summarize(rows)
    ordered = sorted(
        rows,
        key=lambda r: (BAND_ORDER.get(r["band"] or "low", 0), r["rubric_points"] or 0, r["confidence"] or 0),
        reverse=True,
    )
    parts: list[str] = []
    parts.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    parts.append("<title>Evidence brief: %s</title>" % html.escape(entity_name))
    parts.append(
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:960px;"
        "margin:2rem auto;padding:0 1rem;line-height:1.5;color:#1b1b1b}"
        "h1{margin-bottom:.2rem}h2{margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}"
        ".meta{color:#666;font-size:.9rem}.badge{display:inline-block;padding:.1rem .45rem;border-radius:4px;"
        "font-size:.75rem;background:#eee;margin-right:.3rem}"
        ".high{background:#d7f0d7}.moderate{background:#f6edc8}.low{background:#f3dede}"
        ".retracted{background:#f3c6c6;font-weight:600}blockquote{border-left:3px solid #ccc;"
        "margin:.4rem 0;padding-left:.8rem;color:#333}table{border-collapse:collapse}"
        "td,th{border:1px solid #ddd;padding:.3rem .6rem;text-align:left}"
        ".warn{background:#fff4e5;border:1px solid #e0b070;padding:.6rem;border-radius:6px}"
        "</style></head><body>"
    )
    parts.append("<h1>Evidence brief: %s</h1>" % html.escape(entity_name))
    parts.append(
        "<div class='meta'>Ontology id <code>%s</code> · generated %s UTC · %d claims / %d documents</div>"
        % (
            html.escape(entity_id),
            clock.now().strftime("%Y-%m-%d %H:%M"),
            summary["claims"],
            summary["documents"],
        )
    )
    if summary["retractions"]:
        parts.append(
            "<p class='warn'><strong>Integrity alerts:</strong> %d claim(s) come from retracted, "
            "flagged, or retraction-notice records. Read those rows before citing anything.</p>"
            % len(summary["retractions"])
        )
    parts.append("<h2>Snapshot</h2><table><tr><th>Design</th><th>Claims</th></tr>")
    for design, count in sorted(summary["designs"].items()):
        parts.append("<tr><td>%s</td><td>%d</td></tr>" % (html.escape(design), count))
    parts.append("</table>")
    parts.append(
        "<h2>Outcome table</h2><table><tr><th>Outcome</th><th>increase</th><th>decrease</th>"
        "<th>none</th><th>mixed</th></tr>"
    )
    for outcome in sorted(summary["directions"]):
        d = summary["directions"][outcome]
        parts.append(
            "<tr><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td></tr>"
            % (
                html.escape(outcome),
                d.get("increase", 0),
                d.get("decrease", 0),
                d.get("none", 0),
                d.get("mixed", 0),
            )
        )
    parts.append("</table>")
    if summary["contradictions"]:
        parts.append(
            "<p class='warn'>Contradictory directions detected for: %s</p>"
            % html.escape(", ".join(summary["contradictions"]))
        )
    parts.append("<h2>Claims</h2>")
    for row in ordered[:25]:
        link = _link(row)
        title = html.escape(row["title"] or "")
        if link:
            title = "<a href='%s'>%s</a>" % (html.escape(link), title)
        parts.append(
            "<p><span class='badge %s'>%s</span>"
            % (html.escape(row["band"] or "low"), html.escape(row["band"] or "low"))
        )
        parts.append("<span class='badge'>%s</span>" % html.escape(row["design"] or "unknown"))
        parts.append("<span class='badge'>%s</span>" % html.escape(row["role"] or "intervention"))
        if row["retraction_status"] in {"retracted", "concern", "retraction_notice"}:
            parts.append("<span class='badge retracted'>%s</span>" % html.escape(row["retraction_status"]))
        parts.append(
            "<strong>%s</strong> → %s / %s</p>"
            % (html.escape(row["entity_id"]), html.escape(row["direction"]), html.escape(row["outcome"]))
        )
        parts.append(
            "<div class='meta'>%s · %s · n=%s</div>"
            % (title, html.escape(str(row["journal"] or "")), row["sample_size"] or "?")
        )
        parts.append("<blockquote>%s</blockquote>" % html.escape(_evidence(row)))
    if reconciliation is not None:
        parts.extend(_reconciliation_html(reconciliation))
    parts.append("<h2>Method and limitations</h2>")
    if reconciliation is None:
        parts.append(
            "<p>Rule-based extraction over title/abstract text. It does not read full methods, so "
            "randomization, blinding, and preregistration are under-detected; directions are "
            "sentence-level and ignore dose, route, and comparator.</p>"
        )
    else:
        parts.append(
            "<p>Claims are extracted from title/abstract; the optional reconciliation compares them "
            "with PMC open-access JATS. It does not read the publisher PDF, tables or figures; "
            "directions are sentence-level and ignore dose, route, and comparator.</p>"
        )
    parts.append("<p class='meta'>%s</p>" % html.escape(DISCLAIMER))
    parts.append("</body></html>")
    return "".join(parts)


def render_entity(
    store: Store,
    entity_id: str,
    name: str,
    out_dir: Path,
    reconciliation: dict[str, Any] | None = None,
) -> dict[str, str]:
    rows = _rows(store, entity_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = entity_id.split(":")[-1].lower()
    md_path = out_dir / ("%s.md" % slug)
    html_path = out_dir / ("%s.html" % slug)
    md_path.write_text(render_markdown(name, entity_id, rows, reconciliation), encoding="utf-8")
    html_path.write_text(render_html(name, entity_id, rows, reconciliation), encoding="utf-8")
    return {"markdown": str(md_path), "html": str(html_path), "claims": str(len(rows))}


def render_all(
    store: Store,
    out_dir: Path,
    *,
    include_fulltext: bool = False,
) -> dict[str, dict[str, str]]:
    errors = store.provenance_errors()
    if errors:
        raise RuntimeError("provenance check failed for %d claims: %s" % (len(errors), errors[:3]))
    reconciliation_by_entity: dict[str, dict[str, Any]] = {}
    if include_fulltext:
        reconciliation_by_entity = reconcile.by_entity(reconcile.build_report(store))
    cur = store.conn.execute("SELECT DISTINCT entity_id FROM claims ORDER BY entity_id")
    names = {row[0]: row[0].split(":")[-1] for row in cur}
    try:
        from .config import ontology
        from .nlp import Lexicon

        lex = Lexicon(ontology())
        names = {eid: lex.entity(eid).get("name", eid) for eid in names}
    except Exception:
        pass
    rendered: dict[str, dict[str, str]] = {}
    for entity_id, name in names.items():
        if include_fulltext:
            reconciliation = reconciliation_by_entity.get(entity_id, reconcile.empty_entity(entity_id))
        else:
            reconciliation = None
        rendered[entity_id] = render_entity(store, entity_id, name, out_dir, reconciliation)
    return rendered
