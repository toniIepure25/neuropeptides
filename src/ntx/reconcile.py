"""Claim-level reconciliation between abstracts and open-access full text.

The layer is deliberately descriptive.  A finding says that two stored layers
disagree, or that one layer has no comparable claim; it does not prove that the
abstract is wrong or that the full-text sentence is true.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from . import clock
from .store import Store

REPORT_SCHEMA = "ntx-fulltext-report-v4"
STATUSES = (
    "corroborated",
    "direction_conflict",
    "abstract_only",
    "fulltext_only",
    "indeterminate",
)
REASONS = (
    "direction_conflict",
    "significance_conflict",
    "sample_size_discrepancy",
    "effect_size_discrepancy",
    "effect_type_mismatch",
    "effect_ambiguous",
    "comparator_mismatch",
    "dose_route_mismatch",
    "population_mismatch",
    "timepoint_mismatch",
    "hedging_mismatch",
    "discussion_only",
    "abstract_sentence_in_fulltext",
    "mixed_directions",
)
SEVERITIES = ("info", "low", "medium", "high")
SEVERITY_ORDER = {name: index for index, name in enumerate(SEVERITIES)}
SEVERITY_BY_REASON = {
    "direction_conflict": "high",
    "significance_conflict": "high",
    "sample_size_discrepancy": "medium",
    "effect_size_discrepancy": "medium",
    "effect_type_mismatch": "low",
    "effect_ambiguous": "low",
    "comparator_mismatch": "low",
    "dose_route_mismatch": "low",
    "population_mismatch": "low",
    "timepoint_mismatch": "low",
    "hedging_mismatch": "low",
    "discussion_only": "low",
    "abstract_sentence_in_fulltext": "low",
    "mixed_directions": "low",
}
COMPARABLE_DIRECTIONS = {"increase", "decrease", "none"}
COMPARABLE_EFFECT_TYPES = frozenset(
    {"smd", "mean_difference", "odds_ratio", "risk_ratio", "hazard_ratio", "beta", "correlation"}
)
MAX_REFS = 5
MAX_EXCERPT = 320
MIN_ECHO_CHARS = 40


def _normalize_match(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _excerpt(value: str) -> str:
    text = (value or "").strip()
    if len(text) > MAX_EXCERPT:
        return text[: MAX_EXCERPT - 3] + "..."
    return text


def _claim_ref(row: dict[str, Any], source: str) -> dict[str, Any]:
    sentence = row.get("sentence") or ""
    return {
        "source": source,
        "claim_id": row.get("claim_id"),
        "direction": row.get("direction") or "unclear",
        "outcome": row.get("outcome") or "other",
        "section": row.get("section") or ("abstract" if source == "abstract" else "other"),
        "start": row.get("start"),
        "end": row.get("end"),
        "sample_size": row.get("sample_size"),
        "p_value": row.get("p_value"),
        "hedged": bool(row.get("hedged")),
        "role": row.get("role") or "intervention",
        "confidence": row.get("confidence"),
        "effect": _effect(row),
        "effects": _effects(row),
        "quote": sentence,
        "excerpt": _excerpt(sentence),
    }


def _quant(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("quant")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _effects(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every deduplicated effect recorded for a claim row."""
    effects = _quant(row).get("effects")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, Any, Any, Any]] = set()
    if isinstance(effects, list):
        for item in effects:
            if not isinstance(item, dict) or not item.get("type"):
                continue
            effect = {
                "type": str(item.get("type")),
                "value": item.get("value"),
                "ci_low": item.get("ci_low"),
                "ci_high": item.get("ci_high"),
            }
            key = (str(effect["type"]), effect["value"], effect["ci_low"], effect["ci_high"])
            if key not in seen:
                seen.add(key)
                normalized.append(effect)
    if normalized:
        return normalized
    if row.get("effect_type") and row.get("effect_size") is not None:
        return [
            {
                "type": row.get("effect_type"),
                "value": row.get("effect_size"),
                "ci_low": row.get("effect_ci_low"),
                "ci_high": row.get("effect_ci_high"),
            }
        ]
    return []


def _effect(row: dict[str, Any]) -> dict[str, Any] | None:
    effects = _effects(row)
    return effects[0] if effects else None


def _all_effects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, Any, Any, Any]] = set()
    for row in _ordered(rows):
        for effect in _effects(row):
            key = (str(effect["type"]), effect["value"], effect["ci_low"], effect["ci_high"])
            if key not in seen:
                seen.add(key)
                result.append(effect)
    return result


def _effect_comparison(abstract: list[dict[str, Any]], fulltext: list[dict[str, Any]]) -> dict[str, Any]:
    a_effects = _all_effects(abstract)
    f_effects = _all_effects(fulltext)
    a_comparable = [effect for effect in a_effects if effect["type"] in COMPARABLE_EFFECT_TYPES]
    f_comparable = [effect for effect in f_effects if effect["type"] in COMPARABLE_EFFECT_TYPES]
    payload: dict[str, Any] = {
        "abstract": a_comparable[0] if a_comparable else (a_effects[0] if a_effects else None),
        "fulltext": f_comparable[0] if f_comparable else (f_effects[0] if f_effects else None),
        "abstract_effects": a_effects,
        "fulltext_effects": f_effects,
        "ci_overlap": None,
    }
    if not a_effects or not f_effects:
        return {"status": "insufficient", **payload}
    if len(a_comparable) > 1 or len(f_comparable) > 1:
        return {"status": "ambiguous", **payload}
    if not a_comparable or not f_comparable:
        return {"status": "insufficient", **payload}
    a_effect, f_effect = a_comparable[0], f_comparable[0]
    payload["abstract"] = a_effect
    payload["fulltext"] = f_effect
    if a_effect.get("type") != f_effect.get("type"):
        return {"status": "type_mismatch", **payload}
    a_low, a_high = a_effect.get("ci_low"), a_effect.get("ci_high")
    f_low, f_high = f_effect.get("ci_low"), f_effect.get("ci_high")
    if a_low is None or a_high is None or f_low is None or f_high is None:
        return {"status": "insufficient", **payload}
    payload["ci_overlap"] = float(a_low) <= float(f_high) and float(f_low) <= float(a_high)
    return {"status": "comparable", **payload}


def _context_values(rows: list[dict[str, Any]], field: str) -> set[str]:
    values: set[str] = set()
    for row in rows:
        contexts = _quant(row).get("contexts")
        if not isinstance(contexts, dict):
            continue
        item = contexts.get(field)
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            normalized = _normalize_match(item["text"])
            if normalized:
                values.add(normalized)
    return values


def _context_mismatches(abstract: list[dict[str, Any]], fulltext: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for field, reason in (
        ("comparator", "comparator_mismatch"),
        ("dose", "dose_route_mismatch"),
        ("route", "dose_route_mismatch"),
        ("population", "population_mismatch"),
        ("timepoint", "timepoint_mismatch"),
    ):
        a_values = _context_values(abstract, field)
        f_values = _context_values(fulltext, field)
        if a_values and f_values and a_values.isdisjoint(f_values):
            reasons.append(reason)
    return reasons


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (row.get("start") or 0, row.get("end") or 0))


def _refs(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    return [_claim_ref(row, source) for row in _ordered(rows)[:MAX_REFS]]


def _directions(rows: list[dict[str, Any]]) -> set[str]:
    return {row.get("direction") or "unclear" for row in rows}


def _p_values(rows: list[dict[str, Any]]) -> list[float]:
    return [float(row["p_value"]) for row in rows if row.get("p_value") is not None]


def _sample_sizes(rows: list[dict[str, Any]]) -> list[int]:
    return [int(row["sample_size"]) for row in rows if row.get("sample_size") is not None]


def _significance_conflict(abstract: list[dict[str, Any]], fulltext: list[dict[str, Any]]) -> bool:
    a_values = _p_values(abstract)
    f_values = _p_values(fulltext)
    if not a_values or not f_values:
        return False
    return (min(a_values) < 0.05) != (min(f_values) < 0.05)


def _sample_size_discrepancy(abstract: list[dict[str, Any]], fulltext: list[dict[str, Any]]) -> bool:
    a_values = _sample_sizes(abstract)
    f_values = _sample_sizes(fulltext)
    if not a_values or not f_values:
        return False
    a_max, f_max = max(a_values), max(f_values)
    largest = max(a_max, f_max)
    return largest >= 10 and abs(a_max - f_max) / largest > 0.20


def _hedging_mismatch(abstract: list[dict[str, Any]], fulltext: list[dict[str, Any]]) -> bool:
    return (
        bool(abstract and fulltext)
        and not any(row.get("hedged") for row in abstract)
        and all(row.get("hedged") for row in fulltext)
    )


def _discussion_only(fulltext: list[dict[str, Any]]) -> bool:
    return bool(fulltext) and all(
        (row.get("section") or "other") in {"discussion", "conclusions"} for row in fulltext
    )


def _mixed_directions(rowset: list[dict[str, Any]]) -> bool:
    directions = _directions(rowset)
    return "increase" in directions and "decrease" in directions


def _status(abstract: list[dict[str, Any]], fulltext: list[dict[str, Any]]) -> str:
    if not abstract:
        return "fulltext_only"
    if not fulltext:
        return "abstract_only"
    a_directions = _directions(abstract)
    f_directions = _directions(fulltext)
    if a_directions & f_directions:
        return "corroborated"
    a_comparable = a_directions & COMPARABLE_DIRECTIONS
    f_comparable = f_directions & COMPARABLE_DIRECTIONS
    if a_comparable and f_comparable and a_comparable.isdisjoint(f_comparable):
        return "direction_conflict"
    return "indeterminate"


def _severity(status: str, reasons: list[str]) -> str:
    if status == "corroborated" and not reasons:
        return "info"
    levels = [SEVERITY_BY_REASON.get(reason, "low") for reason in reasons]
    return max(levels or ["low"], key=lambda name: SEVERITY_ORDER[name])


def _echo_claim_ids(abstract: list[dict[str, Any]], fulltext_text: str | None) -> list[int]:
    if not fulltext_text:
        return []
    normalized_text = _normalize_match(fulltext_text)
    found: list[int] = []
    for row in _ordered(abstract):
        sentence = _normalize_match(row.get("sentence") or "")
        if len(sentence) < MIN_ECHO_CHARS:
            continue
        if sentence in normalized_text and row.get("claim_id") is not None:
            found.append(int(row["claim_id"]))
    return found


def _finding(
    doc_id: str,
    entity_id: str,
    outcome: str,
    abstract: list[dict[str, Any]],
    fulltext: list[dict[str, Any]],
    *,
    doc: dict[str, Any],
    fulltext_text: str | None,
) -> dict[str, Any]:
    status = _status(abstract, fulltext)
    reasons: list[str] = []
    effect_comparison = _effect_comparison(abstract, fulltext)
    if status == "direction_conflict":
        reasons.append("direction_conflict")
    if _significance_conflict(abstract, fulltext):
        reasons.append("significance_conflict")
    if _sample_size_discrepancy(abstract, fulltext):
        reasons.append("sample_size_discrepancy")
    if (
        status == "corroborated"
        and effect_comparison["status"] == "comparable"
        and effect_comparison["ci_overlap"] is False
    ):
        reasons.append("effect_size_discrepancy")
    if effect_comparison["status"] == "type_mismatch":
        reasons.append("effect_type_mismatch")
    if effect_comparison["status"] == "ambiguous":
        reasons.append("effect_ambiguous")
    reasons.extend(_context_mismatches(abstract, fulltext))
    if _hedging_mismatch(abstract, fulltext):
        reasons.append("hedging_mismatch")
    if _discussion_only(fulltext):
        reasons.append("discussion_only")
    if _mixed_directions(abstract) or _mixed_directions(fulltext):
        reasons.append("mixed_directions")
    echo_ids = _echo_claim_ids(abstract, fulltext_text) if status == "abstract_only" else []
    if echo_ids:
        reasons.append("abstract_sentence_in_fulltext")
    reasons = sorted(set(reasons), key=lambda name: REASONS.index(name))
    severity = _severity(status, reasons)

    quote_row: dict[str, Any] | None
    if status == "fulltext_only" or (status == "direction_conflict" and not abstract):
        quote_row = _ordered(fulltext)[0] if fulltext else None
        quote_source = "fulltext"
    else:
        quote_row = _ordered(abstract)[0] if abstract else (_ordered(fulltext)[0] if fulltext else None)
        quote_source = "abstract" if abstract else "fulltext"
    quote = {
        "source": quote_source,
        "claim_id": quote_row.get("claim_id") if quote_row else None,
        "section": (
            quote_row.get("section") if quote_row else ("abstract" if quote_source == "abstract" else "other")
        ),
        "start": quote_row.get("start") if quote_row else None,
        "end": quote_row.get("end") if quote_row else None,
        "text": _excerpt(quote_row.get("sentence") or "") if quote_row else "",
    }

    return {
        "doc_id": doc_id,
        "entity_id": entity_id,
        "outcome": outcome,
        "status": status,
        "severity": severity,
        "reasons": reasons,
        "abstract_count": len(abstract),
        "fulltext_count": len(fulltext),
        "abstract_directions": sorted(_directions(abstract)),
        "fulltext_directions": sorted(_directions(fulltext)),
        "abstract_claims": _refs(abstract, "abstract"),
        "fulltext_claims": _refs(fulltext, "fulltext"),
        "effect_comparison": effect_comparison,
        "echo_claim_ids": echo_ids,
        "quote": quote,
        "title": doc.get("title") or "",
        "pmcid": doc.get("pmcid") or "",
        "pmid": doc.get("pmid") or "",
        "doi": doc.get("doi") or "",
        "retraction_status": doc.get("retraction_status") or "none",
    }


def _load_abstract(store: Store, entity_id: str | None) -> list[dict[str, Any]]:
    query = (
        "SELECT c.*, d.title, d.pmcid, d.pmid, d.doi, d.retraction_status"
        " FROM claims c JOIN documents d ON d.doc_id = c.doc_id"
    )
    params: tuple[Any, ...] = ()
    if entity_id:
        query += " WHERE c.entity_id = ?"
        params = (entity_id,)
    rows = []
    for row in store.conn.execute(query, params):
        item = dict(row)
        item["quant"] = _quant(item)
        rows.append(item)
    return rows


def _load_fulltext(store: Store, entity_id: str | None) -> list[dict[str, Any]]:
    query = (
        "SELECT c.*, d.title, d.pmcid, d.pmid, d.doi, d.retraction_status"
        " FROM fulltext_claims c JOIN documents d ON d.doc_id = c.doc_id"
    )
    params: tuple[Any, ...] = ()
    if entity_id:
        query += " WHERE c.entity_id = ?"
        params = (entity_id,)
    rows = []
    for row in store.conn.execute(query, params):
        item = dict(row)
        item["quant"] = _quant(item)
        rows.append(item)
    return rows


def _content_hash(payload: dict[str, Any]) -> str:
    clone = dict(payload)
    clone.pop("generated_at", None)
    clone.pop("content_hash", None)
    canonical = json.dumps(clone, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def empty_entity(entity_id: str = "") -> dict[str, Any]:
    """Return a zero-valued per-entity reconciliation block."""
    return {
        "entity_id": entity_id,
        "documents": 0,
        "findings": [],
        "counts": dict.fromkeys(STATUSES + REASONS, 0),
        "severity_counts": dict.fromkeys(SEVERITIES, 0),
    }


def _zero_counts() -> dict[str, int]:
    return dict.fromkeys(STATUSES + REASONS, 0)


def _bump(target: dict[str, int], key: str) -> None:
    target[key] = target.get(key, 0) + 1


def build_report(store: Store, *, entity_id: str | None = None) -> dict[str, Any]:
    """Build a deterministic, claim-level abstract/full-text reconciliation."""
    abstract_errors = store.provenance_errors()
    if abstract_errors:
        raise RuntimeError(
            "provenance check failed for %d claims: %s" % (len(abstract_errors), abstract_errors[:3])
        )
    fulltext_errors = store.fulltext_provenance_errors()
    if fulltext_errors:
        raise RuntimeError(
            "full-text provenance check failed for %d claims: %s"
            % (len(fulltext_errors), fulltext_errors[:3])
        )

    docs = {row["doc_id"]: dict(row) for row in store.documents()}
    snapshots = {row["doc_id"]: row for row in store.document_texts()}
    abstract = _load_abstract(store, entity_id)
    fulltext = _load_fulltext(store, entity_id)
    if entity_id:
        relevant = {row["doc_id"] for row in abstract} | {row["doc_id"] for row in fulltext}
        snapshots = {key: value for key, value in snapshots.items() if key in relevant}
    abstract = [row for row in abstract if row["doc_id"] in snapshots]
    fulltext = [row for row in fulltext if row["doc_id"] in snapshots]

    groups: dict[tuple[str, str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"abstract": [], "fulltext": []}
    )
    for row in abstract:
        key = (row["doc_id"], row["entity_id"], row.get("outcome") or "other")
        groups[key]["abstract"].append(row)
    for row in fulltext:
        key = (row["doc_id"], row["entity_id"], row.get("outcome") or "other")
        groups[key]["fulltext"].append(row)

    findings: list[dict[str, Any]] = []
    section_counts: dict[str, int] = defaultdict(int)
    for (doc_id, group_entity, outcome), group in groups.items():
        doc = docs.get(doc_id, {"doc_id": doc_id})
        fulltext_text = (snapshots.get(doc_id) or {}).get("text")
        finding = _finding(
            doc_id,
            group_entity,
            outcome,
            group["abstract"],
            group["fulltext"],
            doc=doc,
            fulltext_text=fulltext_text,
        )
        findings.append(finding)
        for row in group["fulltext"]:
            section = row.get("section") or "other"
            section_counts[section] += 1
    findings.sort(
        key=lambda item: (
            -SEVERITY_ORDER[item["severity"]],
            item["doc_id"],
            item["entity_id"],
            item["outcome"],
        )
    )

    counts = dict.fromkeys(
        (
            "abstract_documents",
            "fulltext_documents",
            "documents_compared",
            "documents",
            "abstract_claims",
            "fulltext_claims",
            "groups",
        ),
        0,
    )
    counts.update(_zero_counts())
    counts["abstract_documents"] = len({row["doc_id"] for row in abstract})
    counts["fulltext_documents"] = len(snapshots)
    counts["documents"] = len(snapshots)
    counts["documents_compared"] = len({finding["doc_id"] for finding in findings})
    counts["abstract_claims"] = len(abstract)
    counts["fulltext_claims"] = len(fulltext)
    counts["groups"] = len(findings)
    for finding in findings:
        _bump(counts, finding["status"])
        for reason in finding["reasons"]:
            if reason != finding["status"]:
                _bump(counts, reason)

    severity_counts = dict.fromkeys(SEVERITIES, 0)
    for finding in findings:
        _bump(severity_counts, finding["severity"])

    findings_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        findings_by_doc[finding["doc_id"]].append(finding)
    items: list[dict[str, Any]] = []
    for doc_id in sorted(snapshots):
        doc = docs.get(doc_id, {"doc_id": doc_id})
        doc_findings = findings_by_doc.get(doc_id, [])
        item_counts = _zero_counts()
        item_severity = dict.fromkeys(SEVERITIES, 0)
        for finding in doc_findings:
            _bump(item_counts, finding["status"])
            for reason in finding["reasons"]:
                if reason != finding["status"]:
                    _bump(item_counts, reason)
            _bump(item_severity, finding["severity"])
        items.append(
            {
                "doc_id": doc_id,
                "pmcid": doc.get("pmcid") or "",
                "title": doc.get("title") or "",
                "retraction_status": doc.get("retraction_status") or "none",
                "abstract_claims": sum(1 for row in abstract if row["doc_id"] == doc_id),
                "fulltext_claims": sum(1 for row in fulltext if row["doc_id"] == doc_id),
                "groups": len(doc_findings),
                "counts": item_counts,
                "severity_counts": item_severity,
            }
        )

    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": clock.iso(),
        "counts": counts,
        "severity_counts": severity_counts,
        "section_counts": dict(sorted(section_counts.items())),
        "items": items,
        "findings": findings,
    }
    payload["content_hash"] = _content_hash(payload)
    return payload


def by_entity(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Group findings into deterministic per-entity reconciliation blocks."""
    grouped: dict[str, dict[str, Any]] = {}
    for finding in payload.get("findings", []):
        entity_id = finding["entity_id"]
        item = grouped.setdefault(entity_id, empty_entity(entity_id))
        item["findings"].append(finding)
        _bump(item["counts"], finding["status"])
        for reason in finding["reasons"]:
            if reason != finding["status"]:
                _bump(item["counts"], reason)
        _bump(item["severity_counts"], finding["severity"])
    for item in grouped.values():
        docs = {finding["doc_id"] for finding in item["findings"]}
        item["documents"] = len(docs)
        item["findings"].sort(
            key=lambda finding: (
                -SEVERITY_ORDER[finding["severity"]],
                finding["doc_id"],
                finding["outcome"],
            )
        )
    return grouped


def should_fail(payload: dict[str, Any], threshold: str) -> bool:
    """Return whether any finding meets or exceeds the requested severity."""
    if threshold == "none":
        return False
    if threshold not in SEVERITY_ORDER:
        raise ValueError("unknown severity threshold: %s" % threshold)
    limit = SEVERITY_ORDER[threshold]
    return any(
        SEVERITY_ORDER.get(finding.get("severity"), 0) >= limit for finding in payload.get("findings", [])
    )


def format_report(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    lines = [
        "# Full-text reconciliation",
        "",
        "Documents: %d | abstract claims: %d | full-text claims: %d | groups: %d"
        % (counts["documents"], counts["abstract_claims"], counts["fulltext_claims"], counts["groups"]),
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status in STATUSES:
        lines.append("| %s | %d |" % (status, counts.get(status, 0)))
    lines.extend(["", "| Severity | Count |", "| --- | ---: |"])
    for severity in SEVERITIES:
        lines.append("| %s | %d |" % (severity, payload["severity_counts"].get(severity, 0)))
    lines.extend(["", "| Reason | Count |", "| --- | ---: |"])
    for reason in REASONS:
        lines.append("| %s | %d |" % (reason, counts.get(reason, 0)))
    lines.extend(["", "| Section | Claims |", "| --- | ---: |"])
    for section, value in payload["section_counts"].items():
        lines.append("| %s | %d |" % (section, value))
    items = payload.get("items", [])
    if items:
        lines.extend(
            [
                "",
                "| Document | Abstract | Full text | Groups | High |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in items[:20]:
            lines.append(
                "| %s | %d | %d | %d | %d |"
                % (
                    item["doc_id"],
                    item["abstract_claims"],
                    item["fulltext_claims"],
                    item["groups"],
                    item["severity_counts"].get("high", 0),
                )
            )
    findings = payload.get("findings", [])
    if findings:
        lines.extend(
            [
                "",
                "| Document | Entity | Outcome | Status | Severity |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for finding in findings[:20]:
            lines.append(
                "| %s | %s | %s | %s | %s |"
                % (
                    finding["doc_id"],
                    finding["entity_id"],
                    finding["outcome"],
                    finding["status"],
                    finding["severity"],
                )
            )
        lines.extend(["", "## Findings", ""])
        for finding in findings[:20]:
            lines.append(
                "- `%s` **%s** / %s: `%s` (%s)"
                % (
                    finding["doc_id"],
                    finding["entity_id"],
                    finding["outcome"],
                    finding["status"],
                    finding["severity"],
                )
            )
            if finding["reasons"]:
                lines.append("  - reasons: " + ", ".join(finding["reasons"]))
            if finding["quote"]["text"]:
                lines.append("  > " + finding["quote"]["text"].replace("\n", " "))
        if len(findings) > 20:
            lines.append("")
            lines.append("_Showing the first 20 of %d findings._" % len(findings))
    else:
        lines.extend(["", "No entity/outcome groups to reconcile."])
    lines.extend(
        [
            "",
            "This report is descriptive. It does not prove that a full-text sentence is true, "
            "nor that the abstract is confirmed.",
            "",
            "content hash: `%s`" % payload["content_hash"],
        ]
    )
    return "\n".join(lines)
