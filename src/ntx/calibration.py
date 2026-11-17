"""Descriptive calibration analysis for directional ledger predictions.

The ledger records a probability for a predicted direction and a binary outcome
(agreement or not). This module only describes how well those probabilities match
observed agreement. It does not establish that the ledger is a random sample, that
predictions were registered before experiments, or that future probabilities are
calibrated.

Rows are clustered by hypothesis (or explicitly by experiment). The bootstrap
resamples whole clusters, because two experiments for one hypothesis are not
independent observations.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
from typing import Any

from . import stats

SCHEMA = "ntx-calibration-v2"
BASELINE_BRIER = 0.25
UNDERPOWERED_N = 10
UNDERPOWERED_CLUSTERS = 10
MIN_BIN_N = 3
GROUP_MIN_N = 3
GROUP_MIN_CLUSTERS = 2
DEFAULT_BINS = 5
DEFAULT_BOOTSTRAP = 500
MAX_BOOTSTRAP = 5000
CLUSTER_MODES = ("hypothesis", "experiment")
SENSITIVITY_NAMES = ("first_outcome", "qc_pass_only", "precise_ci_only")


class CalibrationError(ValueError):
    """Invalid calibration input or report parameters."""


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CalibrationError("%s must be an integer" % field)
    if not minimum <= value <= maximum:
        raise CalibrationError("%s must be in [%d, %d]" % (field, minimum, maximum))
    return value


def _edges(bins: int) -> list[float]:
    return [0.5 + index * (0.5 / bins) for index in range(bins + 1)]


def _bin_index(probability: float, edges: list[float]) -> int:
    return min(max(bisect.bisect_right(edges, probability) - 1, 0), len(edges) - 2)


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _normalized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise CalibrationError("row %d must be an object" % index)
        probability = row.get("probability")
        if (
            not isinstance(probability, (int, float))
            or isinstance(probability, bool)
            or not math.isfinite(probability)
        ):
            raise CalibrationError("row %d probability must be a finite number" % index)
        probability = float(probability)
        if not 0.5 <= probability < 1.0:
            raise CalibrationError("row %d probability must be in [0.5, 1.0)" % index)
        agree = row.get("agree")
        if not isinstance(agree, int) or isinstance(agree, bool) or agree not in (0, 1):
            raise CalibrationError("row %d agree must be 0 or 1" % index)
        cluster = row.get("cluster")
        if not isinstance(cluster, str) or not cluster.strip():
            raise CalibrationError("row %d cluster must be a non-empty string" % index)
        item = dict(row)
        item["probability"] = probability
        item["agree"] = agree
        item["cluster"] = cluster
        item["kind"] = str(item.get("kind") or "unknown")
        item["design"] = str(item.get("design") or "unknown")
        item["qc"] = str(item.get("qc") or "pass")
        item["effect_size"] = _optional_number(item.get("effect_size"))
        item["ci_low"] = _optional_number(item.get("ci_low"))
        item["ci_high"] = _optional_number(item.get("ci_high"))
        out.append(item)
    return out


def _cluster_count(rows: list[dict[str, Any]]) -> int:
    return len({row["cluster"] for row in rows})


def _brier_ece(rows: list[dict[str, Any]], edges: list[float]) -> tuple[float, float]:
    n = len(rows)
    if n == 0:
        return 0.0, 0.0
    grouped: list[list[dict[str, Any]]] = [[] for _ in range(len(edges) - 1)]
    for row in rows:
        grouped[_bin_index(row["probability"], edges)].append(row)
    brier = sum((row["probability"] - row["agree"]) ** 2 for row in rows) / n
    ece = 0.0
    for items in grouped:
        if not items:
            continue
        predicted = sum(item["probability"] for item in items) / len(items)
        observed = sum(item["agree"] for item in items) / len(items)
        ece += (len(items) / n) * abs(predicted - observed)
    return brier, ece


def _aggregate(rows: list[dict[str, Any]], edges: list[float]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "n_clusters": 0,
            "brier": None,
            "brier_skill": None,
            "accuracy": None,
            "ece": None,
        }
    brier, ece = _brier_ece(rows, edges)
    agree = sum(row["agree"] for row in rows)
    return {
        "n": n,
        "n_clusters": _cluster_count(rows),
        "brier": round(brier, 4),
        "brier_skill": round(1.0 - brier / BASELINE_BRIER, 4),
        "accuracy": round(agree / n, 4),
        "ece": round(ece, 4),
    }


def _bootstrap(rows: list[dict[str, Any]], edges: list[float], n_boot: int, seed: int) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["cluster"], []).append(row)
    cluster_ids = sorted(groups)
    rng = random.Random(seed)
    brier_samples: list[float] = []
    skill_samples: list[float] = []
    ece_samples: list[float] = []
    for _ in range(n_boot):
        sample: list[dict[str, Any]] = []
        for _ in range(len(cluster_ids)):
            sample.extend(groups[cluster_ids[rng.randrange(len(cluster_ids))]])
        brier, ece = _brier_ece(sample, edges)
        brier_samples.append(brier)
        skill_samples.append(1.0 - brier / BASELINE_BRIER)
        ece_samples.append(ece)
    return {
        "n_boot": n_boot,
        "seed": seed,
        "brier_ci": [
            round(stats.percentile(sorted(brier_samples), 0.025), 4),
            round(stats.percentile(sorted(brier_samples), 0.975), 4),
        ],
        "brier_skill_ci": [
            round(stats.percentile(sorted(skill_samples), 0.025), 4),
            round(stats.percentile(sorted(skill_samples), 0.975), 4),
        ],
        "ece_ci": [
            round(stats.percentile(sorted(ece_samples), 0.025), 4),
            round(stats.percentile(sorted(ece_samples), 0.975), 4),
        ],
    }


def _precision(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "ci_consistent": 0,
        "ci_imprecise": 0,
        "ci_inconsistent": 0,
        "ci_missing": 0,
    }
    for row in rows:
        low = row.get("ci_low")
        high = row.get("ci_high")
        observed = row.get("observed")
        if low is None or high is None:
            counts["ci_missing"] += 1
        elif (observed == "increase" and low > 0) or (observed == "decrease" and high < 0):
            counts["ci_consistent"] += 1
        elif low <= 0 <= high:
            counts["ci_imprecise"] += 1
        else:
            counts["ci_inconsistent"] += 1
    return counts


def _groups(rows: list[dict[str, Any]], field: str, edges: list[float]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get(field) or "unknown", []).append(row)
    out: dict[str, Any] = {}
    for name, items in sorted(grouped.items()):
        n = len(items)
        clusters = _cluster_count(items)
        payload: dict[str, Any] = {
            "n": n,
            "n_clusters": clusters,
            "descriptive_only": True,
        }
        if n >= GROUP_MIN_N and clusters >= GROUP_MIN_CLUSTERS:
            payload.update(_aggregate(items, edges))
        else:
            payload.update({"brier": None, "ece": None, "accuracy": None})
        out[name] = payload
    return out


def _sensitivity(
    primary: list[dict[str, Any]],
    variants: dict[str, list[dict[str, Any]]],
    edges: list[float],
) -> dict[str, Any]:
    base = _aggregate(primary, edges)
    out: dict[str, Any] = {"primary": base}
    for name in SENSITIVITY_NAMES:
        rows = variants.get(name, [])
        summary = _aggregate(rows, edges)
        summary["descriptive_only"] = True
        if summary["brier"] is not None and base["brier"] is not None:
            summary["delta_brier"] = round(summary["brier"] - base["brier"], 4)
            summary["delta_ece"] = round(float(summary["ece"]) - float(base["ece"]), 4)
        else:
            summary["delta_brier"] = None
            summary["delta_ece"] = None
        out[name] = summary
    return out


def analyze(
    rows: list[dict[str, Any]],
    *,
    pending: int = 0,
    excluded: int = 0,
    cluster: str = "hypothesis",
    bins: int = DEFAULT_BINS,
    n_boot: int = DEFAULT_BOOTSTRAP,
    seed: int = 0,
    missingness: list[dict[str, Any]] | None = None,
    corrections: dict[str, Any] | None = None,
    sensitivity: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return a descriptive, cluster-aware calibration report."""

    bins = _integer(bins, "bins", 2, 10)
    n_boot = _integer(n_boot, "n_boot", 0, MAX_BOOTSTRAP)
    if cluster not in CLUSTER_MODES:
        raise CalibrationError("cluster must be one of: %s" % ", ".join(CLUSTER_MODES))
    if not isinstance(pending, int) or isinstance(pending, bool) or pending < 0:
        raise CalibrationError("pending must be a non-negative integer")
    if not isinstance(excluded, int) or isinstance(excluded, bool) or excluded < 0:
        raise CalibrationError("excluded must be a non-negative integer")
    data = _normalized(rows)
    variants = {
        name: _normalized(items) for name, items in (sensitivity or {}).items() if name in SENSITIVITY_NAMES
    }
    edges = _edges(bins)
    n = len(data)
    n_clusters = _cluster_count(data)
    reasons: list[str] = []
    if n < UNDERPOWERED_N:
        reasons.append("rows<10")
    if n_clusters < UNDERPOWERED_CLUSTERS:
        reasons.append("clusters<10")
    underpowered = bool(reasons)

    precision = _precision(data)
    correction_payload = dict(corrections or {})
    missing_payload = list(missingness or [])

    if n == 0:
        empty = {
            "schema": SCHEMA,
            "n": 0,
            "n_clusters": 0,
            "pending": pending,
            "excluded": excluded,
            "cluster": cluster,
            "bins": bins,
            "bin_edges": [round(edge, 4) for edge in edges],
            "table": [],
            "brier": None,
            "brier_skill": None,
            "brier_skill_ci": None,
            "accuracy": None,
            "accuracy_ci": None,
            "ece": None,
            "mce": None,
            "decomposition": None,
            "bootstrap": None,
            "underpowered": True,
            "underpowered_reasons": reasons or ["rows<10"],
            "descriptive_only": True,
            "sparse_bins": [],
            "warning": "no eligible observations; record a decisive outcome first",
            "precision": precision,
            "corrections": correction_payload,
            "missingness": missing_payload,
            "sensitivity": _sensitivity([], variants, edges),
            "by_kind": {},
            "by_design": {},
        }
        empty["content_hash"] = content_hash(empty)
        return empty

    grouped: list[list[dict[str, Any]]] = [[] for _ in range(bins)]
    for row in data:
        grouped[_bin_index(row["probability"], edges)].append(row)

    table: list[dict[str, Any]] = []
    sparse_bins: list[str] = []
    for index, items in enumerate(grouped):
        label = "%.2f-%.2f" % (edges[index], edges[index + 1])
        count = len(items)
        if count:
            predicted = sum(item["probability"] for item in items) / count
            observed = sum(item["agree"] for item in items) / count
            gap = abs(predicted - observed)
            brier = sum((item["probability"] - item["agree"]) ** 2 for item in items) / count
            sparse = count < MIN_BIN_N
            if sparse:
                sparse_bins.append(label)
            table.append(
                {
                    "bin": label,
                    "low": round(edges[index], 4),
                    "high": round(edges[index + 1], 4),
                    "n": count,
                    "n_clusters": _cluster_count(items),
                    "predicted": round(predicted, 4),
                    "observed": round(observed, 4),
                    "gap": round(gap, 4),
                    "brier": round(brier, 4),
                    "sparse": sparse,
                }
            )
        else:
            table.append(
                {
                    "bin": label,
                    "low": round(edges[index], 4),
                    "high": round(edges[index + 1], 4),
                    "n": 0,
                    "n_clusters": 0,
                    "predicted": None,
                    "observed": None,
                    "gap": None,
                    "brier": None,
                    "sparse": False,
                }
            )

    brier, ece = _brier_ece(data, edges)
    agree = sum(row["agree"] for row in data)
    accuracy = agree / n
    low, high = stats.wilson_interval(agree, n)
    observed_rate = accuracy
    reliability = 0.0
    resolution = 0.0
    for items in grouped:
        if not items:
            continue
        predicted = sum(item["probability"] for item in items) / len(items)
        observed = sum(item["agree"] for item in items) / len(items)
        weight = len(items) / n
        reliability += weight * (predicted - observed) ** 2
        resolution += weight * (observed - observed_rate) ** 2
    uncertainty = observed_rate * (1.0 - observed_rate)

    mce = 0.0
    for items in grouped:
        if not items:
            continue
        predicted = sum(item["probability"] for item in items) / len(items)
        observed = sum(item["agree"] for item in items) / len(items)
        mce = max(mce, abs(predicted - observed))

    if underpowered:
        warning = "underpowered (%s): descriptive only, not evidence of calibration" % ", ".join(reasons)
    else:
        warning = "descriptive only: the ledger is not a random sample and may be selectively reported"
    if sparse_bins:
        warning += "; sparse bin(s): " + ", ".join(sparse_bins)
    if precision["ci_inconsistent"]:
        warning += "; inconsistent CI outcome(s): %d" % precision["ci_inconsistent"]

    bootstrap = None
    if n >= UNDERPOWERED_N and n_clusters >= UNDERPOWERED_CLUSTERS and n_boot > 0:
        bootstrap = _bootstrap(data, edges, n_boot, seed)

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "n": n,
        "n_clusters": n_clusters,
        "pending": pending,
        "excluded": excluded,
        "cluster": cluster,
        "bins": bins,
        "bin_edges": [round(edge, 4) for edge in edges],
        "table": table,
        "brier": round(brier, 4),
        "brier_skill": round(1.0 - brier / BASELINE_BRIER, 4),
        "brier_skill_ci": bootstrap["brier_skill_ci"] if bootstrap else None,
        "accuracy": round(accuracy, 4),
        "accuracy_ci": [round(low, 4), round(high, 4)],
        "ece": round(ece, 4),
        "mce": round(mce, 4),
        "decomposition": {
            "reliability": round(reliability, 4),
            "resolution": round(resolution, 4),
            "uncertainty": round(uncertainty, 4),
        },
        "bootstrap": bootstrap,
        "underpowered": underpowered,
        "underpowered_reasons": reasons,
        "descriptive_only": True,
        "sparse_bins": sparse_bins,
        "warning": warning,
        "precision": precision,
        "corrections": correction_payload,
        "missingness": missing_payload,
        "sensitivity": _sensitivity(data, variants, edges),
        "by_kind": _groups(data, "kind", edges),
        "by_design": _groups(data, "design", edges),
    }
    payload["content_hash"] = content_hash(payload)
    return payload


def content_hash(payload: dict[str, Any]) -> str:
    """Hash a report while ignoring its generation timestamp and hash field."""

    clone = dict(payload)
    clone.pop("generated_at", None)
    clone.pop("content_hash", None)
    canonical = json.dumps(clone, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def format_calibration(payload: dict[str, Any]) -> str:
    lines = ["Calibration (descriptive)", "==========================", ""]
    if payload["n"] == 0:
        lines.append("no eligible observations")
        lines.append(
            "pending: %d  excluded: %d  clusters: %d"
            % (payload["pending"], payload["excluded"], payload["n_clusters"])
        )
        lines.append("warning: " + payload["warning"])
        return "\n".join(lines)

    lines.append(
        "eligible: %d  clusters: %d  pending: %d  excluded: %d  bins: %d"
        % (
            payload["n"],
            payload["n_clusters"],
            payload["pending"],
            payload["excluded"],
            payload["bins"],
        )
    )
    lines.append("Brier:    %.4f (baseline %.2f)" % (payload["brier"], BASELINE_BRIER))
    lines.append("skill:    %.4f" % payload["brier_skill"])
    lines.append(
        "accuracy: %.4f (95%% CI %.4f-%.4f)"
        % (payload["accuracy"], payload["accuracy_ci"][0], payload["accuracy_ci"][1])
    )
    lines.append("ECE:      %.4f   MCE: %.4f" % (payload["ece"], payload["mce"]))
    lines.append(
        "Murphy:   reliability %.4f - resolution %.4f + uncertainty %.4f"
        % (
            payload["decomposition"]["reliability"],
            payload["decomposition"]["resolution"],
            payload["decomposition"]["uncertainty"],
        )
    )
    if payload["bootstrap"]:
        bootstrap = payload["bootstrap"]
        lines.append(
            "bootstrap: Brier 95%% CI %.4f-%.4f, ECE %.4f-%.4f (%d resamples)"
            % (
                bootstrap["brier_ci"][0],
                bootstrap["brier_ci"][1],
                bootstrap["ece_ci"][0],
                bootstrap["ece_ci"][1],
                bootstrap["n_boot"],
            )
        )
    lines.append("")
    lines.append("bin            n  clusters  predicted  observed     gap   brier")
    for entry in payload["table"]:
        if entry["n"] == 0:
            lines.append("%-13s  0         0          -         -       -       -" % entry["bin"])
        else:
            suffix = " *" if entry["sparse"] else ""
            lines.append(
                "%-13s %2d  %8d    %.4f    %.4f  %.4f  %.4f%s"
                % (
                    entry["bin"],
                    entry["n"],
                    entry["n_clusters"],
                    entry["predicted"],
                    entry["observed"],
                    entry["gap"],
                    entry["brier"],
                    suffix,
                )
            )
    if payload["sparse_bins"]:
        lines.append("sparse bins (<%d observations): %s" % (MIN_BIN_N, ", ".join(payload["sparse_bins"])))
    precision = payload["precision"]
    lines.append(
        "precision: consistent %d, imprecise %d, inconsistent %d, missing %d"
        % (
            precision["ci_consistent"],
            precision["ci_imprecise"],
            precision["ci_inconsistent"],
            precision["ci_missing"],
        )
    )
    for name in SENSITIVITY_NAMES:
        variant = payload["sensitivity"][name]
        if variant["brier"] is None:
            lines.append("%s: no rows" % name)
        else:
            lines.append(
                "%s: Brier %.4f (delta %+.4f), ECE %.4f, clusters %d"
                % (
                    name,
                    variant["brier"],
                    variant["delta_brier"] or 0.0,
                    variant["ece"],
                    variant["n_clusters"],
                )
            )
    corrections = payload["corrections"]
    if corrections:
        lines.append(
            "corrections: %d experiment(s) with multiple outcomes, %d outcome record(s)"
            % (
                corrections.get("experiments_with_multiple_outcomes", 0),
                corrections.get("outcomes_total", 0),
            )
        )
    if payload["missingness"]:
        lines.append("missingness by design:")
        for entry in payload["missingness"]:
            fraction = entry.get("eligible_fraction")
            fraction_text = "n/a" if fraction is None else "%.2f" % fraction
            lines.append(
                "  %-12s registered %d  eligible %d  pending %d  excluded %d  fraction %s"
                % (
                    entry["design"],
                    entry["registered"],
                    entry["eligible"],
                    entry["pending"],
                    entry["excluded"],
                    fraction_text,
                )
            )
    lines.append("warning: " + payload["warning"])
    lines.append("note: descriptive only; never proof of calibration or preregistration")
    return "\n".join(lines)
