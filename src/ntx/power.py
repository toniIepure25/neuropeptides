"""Reproducible sample-size worksheets for simple two-arm designs.

This module is deliberately narrow. It computes a normal-approximation sample
size for a two-arm parallel design with equal allocation, either for a mean
difference (Cohen's d) or for a difference in two proportions. It does not
estimate the effect, model clustering, or claim that the assumed effect exists.

The worksheet records every input and is validated by recomputation before it is
attached to a registration.
"""

from __future__ import annotations

import hashlib
import json
import math
from statistics import NormalDist
from typing import Any

SCHEMA = "ntx-power-v1"
_TOP_LEVEL_FIELDS = {
    "schema",
    "power_id",
    "test",
    "sided",
    "alpha",
    "target_power",
    "dropout",
    "effect_size",
    "p1",
    "p2",
    "n_per_arm",
    "n_total",
    "n_per_arm_with_dropout",
    "n_total_with_dropout",
    "method",
    "assumptions",
    "caveats",
}


class PowerError(ValueError):
    """Invalid power-worksheet input or a stale/tampered worksheet."""


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PowerError("%s must be a finite number" % field)
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise PowerError("%s must be a finite number" % field) from exc
    if not math.isfinite(number):
        raise PowerError("%s must be a finite number" % field)
    return number


def _new_power_id(inputs: dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical({"schema": SCHEMA, "inputs": inputs}).encode("utf-8")).hexdigest()
    return "PW-" + digest[:12]


def _z(value: float) -> float:
    return NormalDist().inv_cdf(value)


def calculate(
    test: str,
    *,
    effect_size: float | None = None,
    p1: float | None = None,
    p2: float | None = None,
    alpha: float = 0.05,
    target_power: float = 0.8,
    dropout: float = 0.0,
    sided: str = "two",
) -> dict[str, Any]:
    """Return a deterministic power worksheet.

    ``test`` is ``mean`` or ``proportion``. For ``mean``, ``effect_size`` is a
    positive Cohen's d. For ``proportion``, ``p1`` and ``p2`` are event
    proportions in [0, 1], and their absolute difference is the effect.
    """

    if test not in ("mean", "proportion"):
        raise PowerError("test must be mean or proportion")
    if sided not in ("two", "one"):
        raise PowerError("sided must be two or one")
    alpha = _number(alpha, "alpha")
    target_power = _number(target_power, "target_power")
    dropout = _number(dropout, "dropout")
    if not 0.0 < alpha < 0.5:
        raise PowerError("alpha must be in (0, 0.5)")
    if not 0.5 < target_power < 1.0:
        raise PowerError("target_power must be in (0.5, 1.0)")
    if not 0.0 <= dropout < 1.0:
        raise PowerError("dropout must be in [0, 1)")

    if test == "mean":
        if p1 is not None or p2 is not None:
            raise PowerError("p1 and p2 are only valid for test=proportion")
        effect_size = _number(effect_size, "effect_size")
        if effect_size <= 0.0:
            raise PowerError("effect_size must be > 0")
        inputs: dict[str, Any] = {"effect_size": effect_size}
        raw = 2.0 * ((_z(1.0 - alpha / (2 if sided == "two" else 1)) + _z(target_power)) / effect_size) ** 2
        method = "two-arm parallel mean difference, equal allocation, normal approximation with Cohen's d"
    else:
        if effect_size is not None:
            raise PowerError("effect_size is only valid for test=mean")
        p1 = _number(p1, "p1")
        p2 = _number(p2, "p2")
        if not 0.0 < p1 < 1.0 or not 0.0 < p2 < 1.0:
            raise PowerError("p1 and p2 must be in (0, 1)")
        if p1 == p2:
            raise PowerError("p1 and p2 must differ")
        effect_size = abs(p2 - p1)
        inputs = {"p1": p1, "p2": p2, "effect_size": effect_size}
        pooled = (p1 + p2) / 2.0
        z_alpha = _z(1.0 - alpha / (2 if sided == "two" else 1))
        z_power = _z(target_power)
        numerator = z_alpha * math.sqrt(2.0 * pooled * (1.0 - pooled)) + z_power * math.sqrt(
            p1 * (1.0 - p1) + p2 * (1.0 - p2)
        )
        raw = (numerator / (p2 - p1)) ** 2
        method = (
            "two-arm parallel risk difference, equal allocation, "
            "normal approximation for two independent proportions"
        )

    if not math.isfinite(raw):
        raise PowerError("effect is too small for a finite sample size")
    n_per_arm = max(1, math.ceil(raw))
    n_total = 2 * n_per_arm
    n_per_arm_with_dropout = max(1, math.ceil(n_per_arm / (1.0 - dropout)))
    n_total_with_dropout = 2 * n_per_arm_with_dropout
    inputs.update(
        {
            "sided": sided,
            "alpha": alpha,
            "target_power": target_power,
            "dropout": dropout,
        }
    )
    worksheet: dict[str, Any] = {
        "schema": SCHEMA,
        "power_id": _new_power_id(inputs),
        "test": test,
        "sided": sided,
        "alpha": alpha,
        "target_power": target_power,
        "dropout": dropout,
        "effect_size": effect_size,
        "n_per_arm": n_per_arm,
        "n_total": n_total,
        "n_per_arm_with_dropout": n_per_arm_with_dropout,
        "n_total_with_dropout": n_total_with_dropout,
        "method": method,
        "assumptions": [
            "two independent arms with equal allocation",
            "independent observations and a single primary outcome",
            "the supplied effect size or proportions are the true design values",
            "two-sided or one-sided normal approximation as selected",
        ],
        "caveats": [
            "not valid for paired, crossover, cluster, time-to-event or ordinal designs",
            "no continuity correction, stratification or missing-data model",
            "dropout is inflated by division only; it is not a missing-data mechanism",
            "the calculation does not establish that the assumed effect is real",
        ],
    }
    if test == "proportion":
        worksheet["p1"] = p1
        worksheet["p2"] = p2
    return worksheet


def validate_power(payload: Any) -> dict[str, Any]:
    """Validate a worksheet by recomputing it from its declared inputs."""

    if not isinstance(payload, dict):
        raise PowerError("power worksheet must be an object")
    unknown = set(payload) - _TOP_LEVEL_FIELDS
    if unknown:
        raise PowerError("unknown power worksheet field(s): %s" % ", ".join(sorted(unknown)))
    if payload.get("schema") != SCHEMA:
        raise PowerError("power worksheet schema must be %s" % SCHEMA)
    test = payload.get("test")
    if test not in ("mean", "proportion"):
        raise PowerError("test must be mean or proportion")
    sided = payload.get("sided")
    if not isinstance(sided, str):
        raise PowerError("sided must be two or one")
    alpha = _number(payload.get("alpha"), "alpha")
    target_power = _number(payload.get("target_power"), "target_power")
    dropout = _number(payload.get("dropout"), "dropout")
    computed = calculate(
        test,
        effect_size=payload.get("effect_size") if test == "mean" else None,
        p1=payload.get("p1") if test == "proportion" else None,
        p2=payload.get("p2") if test == "proportion" else None,
        alpha=alpha,
        target_power=target_power,
        dropout=dropout,
        sided=sided,
    )
    for field, expected in computed.items():
        if payload.get(field) != expected:
            raise PowerError("power worksheet is stale or tampered: %s" % field)
    return computed


def format_power(payload: dict[str, Any]) -> str:
    test = "mean difference" if payload["test"] == "mean" else "risk difference"
    lines = [
        "Power worksheet",
        "===============",
        "id:       %s" % payload["power_id"],
        "design:   two-arm %s, equal allocation, %s-sided" % (test, payload["sided"]),
        "effect:   %.4f" % payload["effect_size"],
        "alpha:    %.4f" % payload["alpha"],
        "power:    %.4f" % payload["target_power"],
        "dropout:  %.4f" % payload["dropout"],
        "n/arm:    %d (%.1f%% dropout: %d)"
        % (
            payload["n_per_arm"],
            payload["dropout"] * 100,
            payload["n_per_arm_with_dropout"],
        ),
        "total:    %d (%.1f%% dropout: %d)"
        % (
            payload["n_total"],
            payload["dropout"] * 100,
            payload["n_total_with_dropout"],
        ),
        "method:   %s" % payload["method"],
        "note:     a design aid, not evidence that the assumed effect exists",
    ]
    return "\n".join(lines)
