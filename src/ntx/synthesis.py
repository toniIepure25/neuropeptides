"""Design-weighted evidence synthesis per entity and outcome.

This is a transparent aggregation, not a truth estimate. Each document counts
once; its weight is the reliability prior attached to the strongest design it
reports for the pair. Directional support is pooled and reported with a
document-level bootstrap interval. The output must always be labelled as a
model-based summary.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from . import stats
from .store import Store

# Deliberately coarse priors: they encode "a meta-analysis is stronger evidence
# than an in-vitro assay", not a validated probability. Changing them changes the
# ranking and therefore requires a changelog entry.
DESIGN_WEIGHTS: dict[str, float] = {
    "meta_analysis": 1.0,
    "systematic_review": 0.95,
    "rct": 0.9,
    "trial": 0.7,
    "cohort": 0.65,
    "case_control": 0.55,
    "cross_sectional": 0.45,
    "animal": 0.35,
    "in_vitro": 0.25,
    "review": 0.2,
    "unknown": 0.2,
}

DIRECTIONAL = {"increase", "decrease"}
DISCLAIMER = (
    "Synthesis is a design-weighted summary of extracted claims, not a "
    "probability that an effect is real. Weights are priors, not calibrated "
    "values, and no human gold set validates them yet."
)


def _doc_direction(directions: set[str]) -> str:
    if "increase" in directions and "decrease" in directions:
        return "mixed"
    for value in ("increase", "decrease", "none", "mixed", "unclear"):
        if value in directions:
            return value
    return "unclear"


def _pooled(pairs: list[tuple[float, str]]) -> float | None:
    support = sum(weight for weight, direction in pairs if direction == "increase")
    refute = sum(weight for weight, direction in pairs if direction == "decrease")
    if support + refute == 0:
        return None
    return support / (support + refute)


def _bootstrap_interval(
    pairs: list[tuple[float, str]], n_boot: int, seed: int
) -> tuple[float | None, float | None]:
    if len(pairs) < 2:
        return None, None
    values: list[float] = []
    for sample in stats.bootstrap_indices(len(pairs), n_boot=n_boot, seed=seed):
        value = _pooled([pairs[index] for index in sample])
        if value is not None:
            values.append(value)
    if len(values) < max(50, n_boot // 10):
        return None, None
    return stats.percentile(values, 0.025), stats.percentile(values, 0.975)


def synthesize(
    store: Store,
    n_boot: int = 2000,
    seed: int = 0,
    include_comparator: bool = False,
) -> list[dict[str, Any]]:
    """Aggregate claims into per-(entity, outcome) evidence summaries."""
    role_clause = "" if include_comparator else " WHERE role='intervention'"
    rows = list(
        store.conn.execute(
            "SELECT doc_id, entity_id, outcome, direction, design, species FROM claims" + role_clause
        )
    )

    # doc -> (entity, outcome) -> strongest weight, direction set, human flag
    per_doc: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        key = (row["entity_id"], row["outcome"] or "other")
        doc = row["doc_id"]
        entry = per_doc[key].setdefault(doc, {"weight": 0.0, "directions": set(), "human": False})
        weight = DESIGN_WEIGHTS.get(row["design"] or "unknown", 0.2)
        entry["weight"] = max(entry["weight"], weight)
        entry["directions"].add(row["direction"] or "unclear")
        if row["species"] == "human":
            entry["human"] = True

    output: list[dict[str, Any]] = []
    for (entity_id, outcome), docs in sorted(per_doc.items()):
        pairs: list[tuple[float, str]] = []
        directions: Counter[str] = Counter()
        human_docs = 0
        sample_docs: dict[str, list[str]] = defaultdict(list)
        for doc_id, entry in sorted(docs.items()):
            direction = _doc_direction(entry["directions"])
            directions[direction] += 1
            if entry["human"]:
                human_docs += 1
            if direction in DIRECTIONAL:
                pairs.append((entry["weight"], direction))
                sample_docs[direction].append(doc_id)
        pooled = _pooled(pairs)
        low, high = _bootstrap_interval(pairs, n_boot=n_boot, seed=seed)
        fragile = pooled is None or (low is not None and high is not None and low <= 0.5 <= high)
        output.append(
            {
                "entity_id": entity_id,
                "outcome": outcome,
                "documents": len(docs),
                "directional_documents": len(pairs),
                "support_documents": directions["increase"],
                "refute_documents": directions["decrease"],
                "neutral_documents": len(docs) - len(pairs),
                "human_documents": human_docs,
                "weighted_support": round(
                    sum(weight for weight, direction in pairs if direction == "increase"), 3
                ),
                "weighted_refute": round(
                    sum(weight for weight, direction in pairs if direction == "decrease"), 3
                ),
                "pooled_support": None if pooled is None else round(pooled, 3),
                "ci_low": None if low is None else round(low, 3),
                "ci_high": None if high is None else round(high, 3),
                "fragile": fragile,
                "directions": dict(directions),
                "sample_doc_ids": {key: value[:3] for key, value in sample_docs.items()},
            }
        )
    output.sort(key=lambda item: (item["entity_id"], -item["documents"], item["outcome"]))
    return output


def format_synthesis(rows: list[dict[str, Any]]) -> str:
    lines = ["Evidence synthesis (design-weighted, uncalibrated)", "=" * 48, ""]
    if not rows:
        lines.append("No claims available for synthesis.")
        return "\n".join(lines)
    lines.append(
        "| Entity | Outcome | Docs | Support | Refute | Neutral | Human | Pooled | 95% CI | Fragile |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |")
    for row in rows:
        pooled = "n/a" if row["pooled_support"] is None else "%.2f" % row["pooled_support"]
        interval = "n/a" if row["ci_low"] is None else "[%.2f, %.2f]" % (row["ci_low"], row["ci_high"])
        lines.append(
            "| %s | %s | %d | %d | %d | %d | %d | %s | %s | %s |"
            % (
                row["entity_id"],
                row["outcome"],
                row["documents"],
                row["support_documents"],
                row["refute_documents"],
                row["neutral_documents"],
                row["human_documents"],
                pooled,
                interval,
                "yes" if row["fragile"] else "no",
            )
        )
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
