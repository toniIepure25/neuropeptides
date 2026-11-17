"""Self-contained local HTML dashboard for a built database."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from . import __version__, reconcile
from .quality import build_quality
from .store import Store

_STYLE = """
:root { color-scheme: light dark; --fg: #1c1c1c; --muted: #666; --line: #d8d8d8;
        --pass: #176b3a; --warn: #8a5a00; --fail: #a11b1b; --bg: #fff; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #eee; --muted: #aaa; --line: #444; --bg: #151515; }
}
body { font: 15px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg);
       margin: 2rem auto; max-width: 1100px; padding: 0 1rem; }
h1, h2 { line-height: 1.2; }
h1 { font-size: 1.5rem; margin-bottom: .2rem; }
h2 { font-size: 1.15rem; margin-top: 2rem; border-bottom: 1px solid var(--line); padding-bottom: .25rem; }
.muted { color: var(--muted); }
table { border-collapse: collapse; width: 100%; margin: .8rem 0; }
th, td { text-align: left; border-bottom: 1px solid var(--line); padding: .35rem .45rem; vertical-align: top; }
th { font-weight: 600; }
.pass { color: var(--pass); font-weight: 600; }
.warn { color: var(--warn); font-weight: 600; }
.fail { color: var(--fail); font-weight: 600; }
.not_configured { color: var(--muted); font-weight: 600; }
code { font-size: .92em; }
blockquote { margin: .3rem 0; padding-left: .7rem; border-left: 3px solid var(--line); color: var(--muted); }
"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _status(value: str) -> str:
    return '<span class="%s">%s</span>' % (_e(value), _e(value))


def render_dashboard(
    store: Store,
    out_dir: Path,
    *,
    gold: list[dict[str, Any]] | None = None,
    max_findings: int = 25,
) -> Path:
    """Write ``index.html`` and return its path. The page has no remote assets."""
    quality = build_quality(store, gold)
    report = reconcile.build_report(store)
    counts = quality["counts"]
    findings = report["findings"][:max_findings]
    claims = list(
        store.conn.execute(
            "SELECT doc_id, entity_id, direction, outcome, effect_type, effect_size, sentence"
            " FROM claims ORDER BY claim_id LIMIT 10"
        )
    )

    gates = "\n".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (_e(gate["name"]), _status(gate["status"]), _e(gate["detail"]))
        for gate in quality["gates"]
    )
    finding_rows = "\n".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><blockquote>%s</blockquote></td></tr>"
        % (
            _e(finding["doc_id"]),
            _e(finding["entity_id"]),
            _e(finding["outcome"]),
            _e(finding["status"]),
            _status(finding["severity"]),
            _e((finding.get("quote") or {}).get("text", "")),
        )
        for finding in findings
    )
    effect_rows = "\n".join(
        "<tr><td>%s</td><td>%s</td></tr>" % (_e(name), _e(number))
        for name, number in sorted(counts.get("effect_types", {}).items())
    )
    claim_rows = "\n".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (
            _e(row["doc_id"]),
            _e(row["entity_id"]),
            _e(row["direction"]),
            _e(row["outcome"]),
            _e(row["effect_type"] or ""),
            _e(row["sentence"]),
        )
        for row in claims
    )
    body = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ntx dashboard %s</title><style>%s</style></head>
<body>
<h1>ntx dashboard</h1>
<p class="muted">v%s &middot; schema %s &middot; generated %s &middot; hash <code>%s</code></p>
<p>Overall quality: <strong>%s</strong></p>
<h2>Counts</h2>
<table>
<tr><th>Documents</th><td>%d</td><th>Entities</th><td>%d</td></tr>
<tr><th>Claims</th><td>%d</td><th>Mentions</th><td>%d</td></tr>
<tr><th>Full-text documents</th><td>%d</td><th>Full-text claims</th><td>%d</td></tr>
<tr><th>Effect claims</th><td>%d</td><th>Effect coverage</th><td>%s</td></tr>
</table>
<h2>Quality gates</h2>
<table><tr><th>Gate</th><th>Status</th><th>Detail</th></tr>
%s
</table>
<h2>Effect types</h2>
<table><tr><th>Type</th><th>Claims</th></tr>
%s
</table>
<h2>Recent claims</h2>
<table><tr><th>Document</th><th>Entity</th><th>Direction</th><th>Outcome</th><th>Effect</th><th>Sentence</th></tr>
%s
</table>
<h2>Reconciliation</h2>
<p class="muted">Descriptive only: a finding is not proof that the abstract is wrong or the full text is true.</p>
<table><tr><th>Document</th><th>Entity</th><th>Outcome</th><th>Status</th><th>Severity</th><th>Quote</th></tr>
%s
</table>
<p class="muted">Showing %d of %d findings. Full text is never redistributed by this page.</p>
</body></html>
""" % (
        _e(__version__),
        _STYLE,
        _e(__version__),
        _e(store.schema_version),
        _e(quality["generated_at"]),
        _e(quality["content_hash"][:16]),
        "yes" if quality["ok"] else "no",
        counts.get("documents", 0),
        counts.get("entities", 0),
        counts.get("claims", 0),
        counts.get("mentions", 0),
        counts.get("fulltext_documents", 0),
        counts.get("fulltext_claims", 0),
        counts.get("effect_claims", 0),
        counts.get("effect_coverage", 0.0),
        gates,
        effect_rows or '<tr><td colspan="2" class="muted">none</td></tr>',
        claim_rows or '<tr><td colspan="6" class="muted">none</td></tr>',
        finding_rows or '<tr><td colspan="6" class="muted">none</td></tr>',
        len(findings),
        len(report["findings"]),
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.html"
    target.write_text(body, encoding="utf-8")
    return target
