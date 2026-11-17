"""Self-contained HTML rendering for descriptive calibration reports."""

from __future__ import annotations

import math
from html import escape
from typing import Any


def _number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    return ("%." + str(digits) + "f") % float(value)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join("<th>%s</th>" % escape(header) for header in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % cell for cell in row) for row in rows)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, body)


def _svg(payload: dict[str, Any]) -> str:
    left, right, top, bottom = 64.0, 500.0, 34.0, 330.0

    def x(value: float) -> float:
        return left + (value - 0.5) / 0.5 * (right - left)

    def y(value: float) -> float:
        return bottom - value * (bottom - top)

    parts = [
        '<svg class="plot" viewBox="0 0 560 390" role="img" '
        'aria-label="Reliability plot of predicted probability versus observed agreement">',
        '<rect x="%0.1f" y="%0.1f" width="%0.1f" height="%0.1f" class="frame"/>'
        % (left, top, right - left, bottom - top),
    ]
    for value in (0.0, 0.5, 1.0):
        parts.append(
            '<line x1="%0.1f" y1="%0.1f" x2="%0.1f" y2="%0.1f" class="grid"/>'
            % (left, y(value), right, y(value))
        )
        parts.append(
            '<text x="%0.1f" y="%0.1f" class="tick" text-anchor="end">%.1f</text>'
            % (left - 8, y(value) + 4, value)
        )
    for value in (0.5, 0.75, 1.0):
        parts.append(
            '<line x1="%0.1f" y1="%0.1f" x2="%0.1f" y2="%0.1f" class="grid"/>'
            % (x(value), top, x(value), bottom)
        )
        parts.append(
            '<text x="%0.1f" y="%0.1f" class="tick" text-anchor="middle">%.2f</text>'
            % (x(value), bottom + 18, value)
        )
    parts.append(
        '<line x1="%0.1f" y1="%0.1f" x2="%0.1f" y2="%0.1f" class="ideal"/>' % (x(0.5), y(0.5), x(1.0), y(1.0))
    )
    for entry in payload["table"]:
        if not entry["n"]:
            continue
        radius = 4.0 + min(10.0, math.sqrt(entry["n"]) * 2.0)
        parts.append(
            '<circle cx="%0.1f" cy="%0.1f" r="%0.1f" class="point">'
            "<title>%s: predicted %.4f, observed %.4f, n=%d</title></circle>"
            % (
                x(entry["predicted"]),
                y(entry["observed"]),
                radius,
                escape(entry["bin"]),
                entry["predicted"],
                entry["observed"],
                entry["n"],
            )
        )
    parts.append(
        '<text x="%0.1f" y="%0.1f" class="axis" text-anchor="middle">Predicted probability</text>'
        % ((left + right) / 2, 374)
    )
    parts.append(
        '<text x="18" y="%0.1f" class="axis" text-anchor="middle" '
        'transform="rotate(-90 18 %0.1f)">Observed agreement</text>'
        % ((top + bottom) / 2, (top + bottom) / 2)
    )
    parts.append("</svg>")
    return "".join(parts)


def render_html(payload: dict[str, Any]) -> str:
    """Render a deterministic, dependency-free HTML report."""

    warning = escape(payload["warning"])
    metric_rows = [
        ["Eligible rows", str(payload["n"])],
        ["Clusters (%s)" % payload["cluster"], str(payload["n_clusters"])],
        ["Pending", str(payload["pending"])],
        ["Excluded", str(payload["excluded"])],
        ["Brier", _number(payload["brier"])],
        ["Brier skill", _number(payload["brier_skill"])],
        ["Accuracy", _number(payload["accuracy"])],
        ["ECE", _number(payload["ece"])],
        ["MCE", _number(payload["mce"])],
    ]
    if payload["bootstrap"]:
        bootstrap = payload["bootstrap"]
        metric_rows.extend(
            [
                [
                    "Brier 95%% CI (%d resamples)" % bootstrap["n_boot"],
                    "%s to %s" % (_number(bootstrap["brier_ci"][0]), _number(bootstrap["brier_ci"][1])),
                ],
                [
                    "ECE 95% CI",
                    "%s to %s" % (_number(bootstrap["ece_ci"][0]), _number(bootstrap["ece_ci"][1])),
                ],
            ]
        )
    if payload["decomposition"]:
        metric_rows.append(
            [
                "Murphy decomposition",
                "reliability %s - resolution %s + uncertainty %s"
                % (
                    _number(payload["decomposition"]["reliability"]),
                    _number(payload["decomposition"]["resolution"]),
                    _number(payload["decomposition"]["uncertainty"]),
                ),
            ]
        )
    else:
        metric_rows.append(["Murphy decomposition", "-"])

    bin_rows = [
        [
            escape(entry["bin"]),
            str(entry["n"]),
            str(entry["n_clusters"]),
            _number(entry["predicted"]),
            _number(entry["observed"]),
            _number(entry["gap"]),
            _number(entry["brier"]),
            "yes" if entry["sparse"] else "",
        ]
        for entry in payload["table"]
    ]
    precision = payload["precision"]
    precision_rows = [
        ["CI consistent", str(precision["ci_consistent"])],
        ["CI imprecise", str(precision["ci_imprecise"])],
        ["CI inconsistent", str(precision["ci_inconsistent"])],
        ["CI missing", str(precision["ci_missing"])],
    ]
    correction_rows = [
        ["Outcome records", str(payload["corrections"].get("outcomes_total", 0))],
        [
            "Experiments with multiple outcomes",
            str(payload["corrections"].get("experiments_with_multiple_outcomes", 0)),
        ],
    ]
    sensitivity_rows = []
    for name in ("primary", "first_outcome", "qc_pass_only", "precise_ci_only"):
        variant = payload["sensitivity"].get(name, {})
        sensitivity_rows.append(
            [
                escape(name),
                str(variant.get("n", 0)),
                str(variant.get("n_clusters", 0)),
                _number(variant.get("brier")),
                _number(variant.get("ece")),
                "n/a" if variant.get("delta_brier") is None else "%+.4f" % variant["delta_brier"],
            ]
        )
    missing_rows = [
        [
            escape(entry["design"]),
            str(entry["registered"]),
            str(entry["eligible"]),
            str(entry["pending"]),
            str(entry["excluded"]),
            _number(entry.get("eligible_fraction"), 2),
        ]
        for entry in payload["missingness"]
    ]

    def group_rows(field: str) -> list[list[str]]:
        rows = []
        for name, group in payload[field].items():
            rows.append(
                [
                    escape(name),
                    str(group["n"]),
                    str(group["n_clusters"]),
                    _number(group.get("brier")),
                    _number(group.get("ece")),
                    _number(group.get("accuracy")),
                ]
            )
        return rows

    sections = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>ntx calibration report</title>",
        "<style>",
        "body{font-family:-apple-system,Segoe UI,Arial,sans-serif;margin:32px;color:#17202a}"
        "h1{font-size:24px;margin-bottom:4px}h2{font-size:17px;margin-top:28px}"
        "table{border-collapse:collapse;margin:8px 0 16px;font-size:13px}"
        "th,td{border:1px solid #ccd3da;padding:4px 7px;text-align:right}"
        "th{background:#eef2f5}td:first-child,th:first-child{text-align:left}"
        ".warning{background:#fff4ce;border-left:4px solid #c58b00;padding:10px 12px;margin:14px 0}"
        ".note{color:#4d5a66;font-size:13px}.plot{width:100%;max-width:640px;height:auto;margin:8px 0}"
        ".frame{fill:#fbfcfd;stroke:#8d9aa6}.grid{stroke:#dce3e8;stroke-width:1}"
        ".ideal{stroke:#4d5a66;stroke-dasharray:5 4}.point{fill:#1f6f8b;fill-opacity:.72;stroke:#0e4253}"
        ".tick{font-size:11px;fill:#4d5a66}.axis{font-size:12px;fill:#4d5a66}"
        "code{font-size:12px;word-break:break-all}",
        "</style></head><body>",
        "<h1>Calibration (descriptive)</h1>",
        '<p class="note">Schema <code>%s</code> · cluster <code>%s</code> · generated <code>%s</code></p>'
        % (escape(payload["schema"]), escape(payload["cluster"]), escape(payload.get("generated_at", ""))),
        '<div class="warning">%s</div>' % warning,
        "<h2>Summary</h2>",
        _table(["Measure", "Value"], metric_rows),
        "<h2>Reliability</h2>",
        _svg(payload),
        "<h2>Bins</h2>",
        _table(
            ["Bin", "n", "Clusters", "Predicted", "Observed", "Gap", "Brier", "Sparse"],
            bin_rows,
        ),
        "<h2>Outcome precision</h2>",
        _table(["Class", "Rows"], precision_rows),
        "<h2>Corrections</h2>",
        _table(["Measure", "Value"], correction_rows),
        "<h2>Sensitivity (one choice at a time)</h2>",
        _table(["Variant", "n", "Clusters", "Brier", "ECE", "Delta Brier"], sensitivity_rows),
        "<h2>Missingness by design</h2>",
        _table(
            ["Design", "Registered", "Eligible", "Pending", "Excluded", "Eligible fraction"], missing_rows
        ),
        "<h2>By hypothesis kind</h2>",
        _table(["Kind", "n", "Clusters", "Brier", "ECE", "Accuracy"], group_rows("by_kind")),
        "<h2>By design</h2>",
        _table(["Design", "n", "Clusters", "Brier", "ECE", "Accuracy"], group_rows("by_design")),
        '<p class="note">Descriptive only. No calibration, replication or treatment-effect claim. '
        "Content hash: <code>%s</code></p>" % escape(payload.get("content_hash", "")),
        "</body></html>",
    ]
    return "\n".join(sections)
