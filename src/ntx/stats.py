"""Agreement statistics and bootstrap confidence intervals (stdlib only).

These are the numbers a reviewer will ask for: how much two annotators agree
(Cohen's kappa) and how wide the uncertainty around P/R/F1 is (document-level
bootstrap), given a finite gold set.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def cohen_kappa(pairs: Iterable[tuple[str, str]]) -> dict[str, float | int]:
    """Cohen's kappa for two raters over nominal labels.

    ``pairs`` is a sequence of ``(rater_a, rater_b)`` labels for the same items.
    Items rated by only one annotator must be excluded before calling this.
    """
    data = list(pairs)
    n = len(data)
    if n == 0:
        return {"n": 0, "observed_agreement": 0.0, "expected_agreement": 0.0, "kappa": 0.0}
    labels = sorted({label for pair in data for label in pair})
    counts_a = dict.fromkeys(labels, 0)
    counts_b = dict.fromkeys(labels, 0)
    agree = 0
    for left, right in data:
        counts_a[left] += 1
        counts_b[right] += 1
        agree += left == right
    observed = agree / n
    expected = sum((counts_a[label] / n) * (counts_b[label] / n) for label in labels)
    if expected >= 1.0:
        kappa = 1.0 if observed >= 1.0 else 0.0
    else:
        kappa = (observed - expected) / (1.0 - expected)
    return {
        "n": n,
        "observed_agreement": round(observed, 4),
        "expected_agreement": round(expected, 4),
        "kappa": round(kappa, 4),
    }


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% by default).

    Preferred to the normal approximation for the small audit samples this
    project can afford: it stays inside ``[0, 1]`` and behaves at 0/n.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = (z / denominator) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    position = q * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def bootstrap_indices(n_rows: int, n_boot: int = 2000, seed: int = 0) -> Iterable[list[int]]:
    """Yield document-level bootstrap index samples, deterministic per seed."""
    if n_rows <= 0:
        return
    rng = random.Random(seed)
    for _ in range(n_boot):
        yield [rng.randrange(n_rows) for _ in range(n_rows)]


def bootstrap_prf(
    per_document: Sequence[tuple[int, int, int]],
    n_boot: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, dict[str, float]]:
    """Bootstrap P/R/F1 by resampling documents with replacement.

    ``per_document`` holds ``(tp, fp, fn)`` counts for each document. Resampling
    documents (not claims) preserves the clustered structure of the data.
    """
    rows = list(per_document)
    if not rows:
        return {name: {"value": 0.0, "low": 0.0, "high": 0.0} for name in ("precision", "recall", "f1")}

    total_tp = sum(row[0] for row in rows)
    total_fp = sum(row[1] for row in rows)
    total_fn = sum(row[2] for row in rows)
    point = prf(total_tp, total_fp, total_fn)

    rng = random.Random(seed)
    samples: dict[str, list[float]] = {"precision": [], "recall": [], "f1": []}
    for _ in range(n_boot):
        tp = fp = fn = 0
        for _ in range(len(rows)):
            index = rng.randrange(len(rows))
            tp += rows[index][0]
            fp += rows[index][1]
            fn += rows[index][2]
        precision, recall, f1 = prf(tp, fp, fn)
        samples["precision"].append(precision)
        samples["recall"].append(recall)
        samples["f1"].append(f1)

    out: dict[str, dict[str, float]] = {}
    for index, name in enumerate(("precision", "recall", "f1")):
        ordered = sorted(samples[name])
        out[name] = {
            "value": round(point[index], 4),
            "low": round(percentile(ordered, alpha / 2), 4),
            "high": round(percentile(ordered, 1 - alpha / 2), 4),
        }
    return out
