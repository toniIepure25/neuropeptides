"""Machine-readable quality gates for a built database.

Quality is descriptive: a passing gate means the stored invariants and optional
gold inputs are internally consistent. It is not a claim that the extraction is
correct, and the ``gold`` gate stays ``not_configured`` unless a human gold file
is supplied explicitly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__, clock
from .quant import EFFECT_TYPES
from .store import SCHEMA_VERSION, Store

GATE_STATUSES = ("pass", "warn", "fail", "not_configured")


def _hash(payload: dict[str, Any]) -> str:
    clone = dict(payload)
    clone.pop("generated_at", None)
    clone.pop("content_hash", None)
    canonical = json.dumps(clone, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _gate(name: str, status: str, detail: str, **metrics: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "metrics": metrics}


def _ambiguous_effect_claims(store: Store, table: str) -> int:
    """Count stored claims carrying more than one explicit effect."""
    rows = store.conn.execute("SELECT quant FROM %s WHERE quant IS NOT NULL AND quant != '{}'" % table)
    count = 0
    for row in rows:
        try:
            payload = json.loads(row["quant"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("warnings"):
            count += 1
    return count


def build_quality(
    store: Store,
    gold: list[dict[str, Any]] | None = None,
    *,
    min_effect_coverage: float = 0.2,
    require_gold: bool = False,
) -> dict[str, Any]:
    """Build deterministic gates for schema, provenance, effects and gold."""
    if not 0.0 <= min_effect_coverage <= 1.0:
        raise ValueError("min_effect_coverage must be between 0 and 1")
    counts = store.counts()
    schema_ok = store.schema_version == SCHEMA_VERSION
    abstract_errors = store.provenance_errors()
    fulltext_errors = store.fulltext_provenance_errors()

    effect_type_rows = list(
        store.conn.execute(
            "SELECT effect_type, COUNT(*) AS n FROM claims WHERE effect_type != '' GROUP BY effect_type"
        )
    )
    effect_types = {str(row["effect_type"]): int(row["n"]) for row in effect_type_rows}
    unknown_types = sorted(set(effect_types) - set(EFFECT_TYPES))
    effect_claims = sum(effect_types.values())
    claim_count = int(counts.get("claims", 0))
    effect_coverage = round(effect_claims / claim_count, 3) if claim_count else 0.0
    ambiguous_abstract = _ambiguous_effect_claims(store, "claims")
    ambiguous_fulltext = _ambiguous_effect_claims(store, "fulltext_claims")
    effect_status = "pass"
    effect_reasons: list[str] = []
    if unknown_types:
        effect_status = "fail"
        effect_reasons.append("unknown types: " + ", ".join(unknown_types))
    if claim_count and effect_coverage < min_effect_coverage:
        if effect_status != "fail":
            effect_status = "warn"
        effect_reasons.append("coverage below threshold")
    if ambiguous_abstract or ambiguous_fulltext:
        if effect_status != "fail":
            effect_status = "warn"
        effect_reasons.append(
            "ambiguous effects: %d abstract, %d full-text" % (ambiguous_abstract, ambiguous_fulltext)
        )

    gates = [
        _gate(
            "schema",
            "pass" if schema_ok else "fail",
            "schema v%d" % store.schema_version,
            expected=SCHEMA_VERSION,
            actual=store.schema_version,
        ),
        _gate(
            "provenance",
            "pass" if not abstract_errors and not fulltext_errors else "fail",
            "%d abstract and %d full-text provenance error(s)" % (len(abstract_errors), len(fulltext_errors)),
            abstract_errors=len(abstract_errors),
            fulltext_errors=len(fulltext_errors),
        ),
        _gate(
            "effects",
            effect_status,
            "%d/%d claims carry an explicit effect%s"
            % (
                effect_claims,
                claim_count,
                ("; " + "; ".join(effect_reasons)) if effect_reasons else "",
            ),
            effect_claims=effect_claims,
            claims=claim_count,
            coverage=effect_coverage,
            min_effect_coverage=min_effect_coverage,
            ambiguous_abstract=ambiguous_abstract,
            ambiguous_fulltext=ambiguous_fulltext,
        ),
    ]

    if gold is None:
        gold_payload = {
            "ready": False,
            "status": "not_configured",
            "blockers": ["gold_not_configured"],
            "counts": {},
        }
        gates.append(
            _gate(
                "gold",
                "fail" if require_gold else "not_configured",
                "no gold file supplied",
                ready=False,
                required=require_gold,
            )
        )
    else:
        from .eval import gold_readiness

        gold_payload = gold_readiness(gold)
        gates.append(
            _gate(
                "gold",
                "pass" if gold_payload["ready"] else ("fail" if require_gold else "warn"),
                "human gold ready" if gold_payload["ready"] else "human gold not ready",
                ready=gold_payload["ready"],
                blockers=gold_payload["blockers"],
                required=require_gold,
            )
        )

    failed = [gate["name"] for gate in gates if gate["status"] == "fail"]
    payload: dict[str, Any] = {
        "schema": "ntx-quality-v2",
        "version": __version__,
        "generated_at": clock.iso(),
        "database": str(store.path),
        "thresholds": {"min_effect_coverage": min_effect_coverage, "require_gold": require_gold},
        "counts": {
            **counts,
            "effect_claims": effect_claims,
            "effect_coverage": effect_coverage,
            "effect_types": dict(sorted(effect_types.items())),
            "effect_ambiguous_claims": ambiguous_abstract,
            "effect_ambiguous_fulltext_claims": ambiguous_fulltext,
            "unknown_effect_types": unknown_types,
        },
        "gates": gates,
        "gold": gold_payload,
        "ok": not failed,
        "failed_gates": failed,
    }
    payload["content_hash"] = _hash(payload)
    return payload


def format_quality(payload: dict[str, Any]) -> str:
    lines = ["Quality gates", "=============", ""]
    for gate in payload["gates"]:
        lines.append("%-14s %-14s %s" % (gate["name"], gate["status"], gate["detail"]))
    lines.append("")
    thresholds = payload.get("thresholds", {})
    lines.append("min effect coverage: %.2f" % thresholds.get("min_effect_coverage", 0.0))
    lines.append("require gold: %s" % ("yes" if thresholds.get("require_gold") else "no"))
    lines.append("ok: %s" % ("yes" if payload["ok"] else "no"))
    lines.append("content hash: %s" % payload["content_hash"])
    return "\n".join(lines)
