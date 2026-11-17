"""Paired ablation runner for extraction feature flags.

Each configuration is applied to the same documents and scored against the same
gold file. Deltas are paired at the document level and bootstrapped with the same
resampled document indices, so the interval reflects uncertainty in the corpus,
not run-to-run noise. A configuration that breaks provenance is refused.
"""

from __future__ import annotations

import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from . import eval as eval_mod
from . import stats
from .extract import FeatureFlags
from .models import Document
from .pipeline import process_documents
from .store import Store

CONFIGS: OrderedDict[str, FeatureFlags] = OrderedDict(
    [
        ("baseline", FeatureFlags()),
        ("no_clause", FeatureFlags(clause_direction=False)),
        ("no_role", FeatureFlags(comparator_role=False)),
        ("no_section", FeatureFlags(section_filter=False)),
        ("coref", FeatureFlags(resolve_coref=True)),
        (
            "legacy",
            FeatureFlags(clause_direction=False, comparator_role=False, section_filter=False),
        ),
    ]
)


def _pooled_f1(rows: list[tuple[int, int, int]]) -> float:
    tp = sum(row[0] for row in rows)
    fp = sum(row[1] for row in rows)
    fn = sum(row[2] for row in rows)
    return stats.prf(tp, fp, fn)[2]


def run_ablation(
    docs: list[Document],
    gold: list[dict[str, Any]],
    annotations: dict[str, list[dict[str, Any]]] | None = None,
    configs: list[str] | None = None,
    n_boot: int = 500,
    seed: int = 0,
) -> dict[str, Any]:
    names = configs or list(CONFIGS)
    unknown = [name for name in names if name not in CONFIGS]
    if unknown:
        raise ValueError(
            "unknown configuration(s): %s (valid: %s)" % (", ".join(unknown), ", ".join(CONFIGS))
        )
    if "baseline" not in names:
        names = ["baseline"] + names

    corpus_ids = {doc.doc_id for doc in docs}
    present = [item for item in gold if item["doc_id"] in corpus_ids]
    missing = sorted({item["doc_id"] for item in gold if item["doc_id"] not in corpus_ids})
    if not present:
        raise ValueError("none of the gold documents are present in the corpus")

    results: dict[str, dict[str, Any]] = {}
    counts_by_config: dict[str, dict[str, tuple[int, int, int]]] = {}
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "ablate.db")
            try:
                process_documents(store, docs, annotations or {}, flags=CONFIGS[name])
                errors = store.provenance_errors()
                if errors:
                    raise RuntimeError(
                        "configuration %s has %d provenance error(s): %s" % (name, len(errors), errors[:2])
                    )
                aggregate = eval_mod.evaluate(store, present)
                counts = eval_mod.document_counts(store, present)
                results[name] = {"aggregate": aggregate, "claims": aggregate["pred_claims"]}
                counts_by_config[name] = counts
            finally:
                store.close()

    doc_ids = sorted(counts_by_config["baseline"])
    baseline_rows = [counts_by_config["baseline"][doc_id] for doc_id in doc_ids]
    samples = list(stats.bootstrap_indices(len(doc_ids), n_boot=n_boot, seed=seed))
    base_distribution = [_pooled_f1([baseline_rows[index] for index in sample]) for sample in samples]

    for name in names:
        rows = [counts_by_config[name][doc_id] for doc_id in doc_ids]
        payload = results[name]
        payload["strict_f1"] = payload["aggregate"]["claims_strict"]["f1"]
        if name == "baseline":
            payload["delta"] = {"value": 0.0, "low": 0.0, "high": 0.0}
            continue
        distribution = [_pooled_f1([rows[index] for index in sample]) for sample in samples]
        deltas = sorted(value - base for value, base in zip(distribution, base_distribution, strict=True))
        payload["delta"] = {
            "value": round(payload["strict_f1"] - results["baseline"]["strict_f1"], 4),
            "low": round(stats.percentile(deltas, 0.025), 4),
            "high": round(stats.percentile(deltas, 0.975), 4),
        }
    return {
        "configs": names,
        "documents": len(doc_ids),
        "gold_documents": len(gold),
        "gold_documents_missing": missing,
        "results": results,
    }


def format_ablation(result: dict[str, Any]) -> str:
    lines = [
        "Paired ablation (same documents and gold for every configuration)",
        "=" * 62,
        "",
        "documents: %d | configurations: %d" % (result["documents"], len(result["configs"])),
    ]
    missing = result.get("gold_documents_missing") or []
    if missing:
        lines.append(
            "WARNING: %d of %d gold documents are absent from the corpus (%s); "
            "they are excluded from scoring."
            % (len(missing), result["gold_documents"], ", ".join(missing[:5]))
        )
    lines += [
        "",
        "| Configuration | Claims | P | R | F1 | dF1 vs baseline [95% CI] |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for name in result["configs"]:
        payload = result["results"][name]
        aggregate = payload["aggregate"]
        strict = aggregate["claims_strict"]
        delta = payload["delta"]
        if name == "baseline":
            delta_text = "reference"
        else:
            delta_text = "%+.3f [%+.3f, %+.3f]" % (delta["value"], delta["low"], delta["high"])
        lines.append(
            "| %s | %d | %.3f | %.3f | %.3f | %s |"
            % (
                name,
                payload["claims"],
                strict["precision"],
                strict["recall"],
                strict["f1"],
                delta_text,
            )
        )
    lines.append("")
    lines.append(
        "Negative deltas are expected when a feature is removed; a positive delta "
        "for a removal is evidence the feature is mis-specified, not a win."
    )
    return "\n".join(lines)
