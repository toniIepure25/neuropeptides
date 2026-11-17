"""Conservative quantitative claim details.

The extractor is intentionally narrow: an effect is recorded only when the
sentence carries an explicit effect label (for example "Cohen's d", "OR" or
"mean difference") and a numeric value. Context fields (comparator, dose, route,
timepoint, population) are recorded only when a matching phrase has an exact
character span. Absence is represented by an empty ``quant`` object, never by a
guessed value.
"""

from __future__ import annotations

import math
import re
from typing import Any

EFFECT_TYPES = (
    "smd",
    "mean_difference",
    "odds_ratio",
    "risk_ratio",
    "hazard_ratio",
    "beta",
    "correlation",
    "percent_change",
    "other",
)
CONTEXT_FIELDS = ("comparator", "dose", "route", "timepoint", "population")
RATIO_TYPES = frozenset({"odds_ratio", "risk_ratio", "hazard_ratio"})
QUANT_SCHEMA = "ntx-quant-v1"
QUANT_WARNINGS = frozenset({"multiple_effects", "multiple_effect_types"})

_VALUE = r"[−-]?\d+(?:\.\d+)?"
_CI = re.compile(
    r"(?:95\s*%\s*)?(?:CI|confidence\s+interval)\s*[:=]?\s*[\(\[]?\s*"
    r"(?P<low>" + _VALUE + r")\s*(?:to|–|—|-|,)\s*(?P<high>" + _VALUE + r")\s*[\)\]]?",
    re.IGNORECASE,
)
_BRACKET_CI = re.compile(
    r"[\(\[]\s*(?P<low>" + _VALUE + r")\s*(?:to|–|—|-|,)\s*"
    r"(?P<high>" + _VALUE + r")\s*[\)\]]"
)

_EFFECT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "smd",
        re.compile(
            r"\b(?:cohen'?s\s+d|cohen\s+d|hedges'?\s+g|standardized\s+mean\s+difference|"
            r"(?i:SMD))\s*(?:=|:|\bof\b|\bwas\b|\bwere\b)?\s*(?P<value>" + _VALUE + r")",
            re.IGNORECASE,
        ),
    ),
    (
        "mean_difference",
        re.compile(
            r"\b(?:mean\s+difference|difference\s+in\s+means|(?i:MD)\s*=)\s*"
            r"(?:=|:|\bof\b|\bwas\b|\bwere\b)?\s*(?P<value>" + _VALUE + r")",
            re.IGNORECASE,
        ),
    ),
    (
        "odds_ratio",
        re.compile(
            r"\b(?:odds\s+ratio|(?-i:OR))\s*(?:=|:|\bof\b|\bwas\b|\bwere\b)?\s*"
            r"(?P<value>\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
    ),
    (
        "risk_ratio",
        re.compile(
            r"\b(?:risk\s+ratio|relative\s+risk|(?-i:RR))\s*(?:=|:|\bof\b|\bwas\b|\bwere\b)?\s*"
            r"(?P<value>\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
    ),
    (
        "hazard_ratio",
        re.compile(
            r"\b(?:hazard\s+ratio|(?-i:HR))\s*(?:=|:|\bof\b|\bwas\b|\bwere\b)?\s*"
            r"(?P<value>\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
    ),
    (
        "beta",
        re.compile(
            r"\b(?:β|beta)\s*(?:=|:|\bof\b)\s*(?P<value>" + _VALUE + r")",
            re.IGNORECASE,
        ),
    ),
    (
        "correlation",
        re.compile(
            r"\b(?:pearson'?s\s+r|correlation\s+coefficient|(?-i:r))\s*=\s*"
            r"(?P<value>" + _VALUE + r")",
            re.IGNORECASE,
        ),
    ),
    (
        "percent_change",
        re.compile(
            r"\b(?:percent\s+change|%)\s*(?:=|:|\bof\b|\bwas\b|\bwere\b)?\s*"
            r"(?P<value>" + _VALUE + r")\s*%",
            re.IGNORECASE,
        ),
    ),
    (
        "percent_change",
        re.compile(
            r"\b(?P<verb>increased|decreased|reduced|improved|changed|elevated|lowered)\s+by\s+"
            r"(?P<value>\d+(?:\.\d+)?)\s*%",
            re.IGNORECASE,
        ),
    ),
]

_COMPARATOR = re.compile(
    r"(?:\bcompared\s+(?:with|to)\b|\bversus\b|\bvs\.?\b|\brelative\s+to\b|\bthan\b)\s+"
    r"(?P<text>[A-Za-z][A-Za-z0-9µμ%/+\- ]{1,50}?)"
    r"(?=\s*(?:[,.;()\]]|$|\b(?:alone|at|for|in|on|during|after|before|and|or)\b))",
    re.IGNORECASE,
)
_COMPARATOR_ALONE = re.compile(
    r"(?P<text>[A-Za-z][A-Za-z0-9µμ%/+\- ]{1,50}?)\s+alone\b",
    re.IGNORECASE,
)
_DOSE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg/kg|mg|µg/kg|µg|μg/kg|μg|ug/kg|ug|ng/kg|ng|IU|units?)(?:/day)?\b",
    re.IGNORECASE,
)
_ROUTE = re.compile(
    r"\b(?:intranasal(?:ly)?|intravenous(?:ly)?|i\.v\.|oral(?:ly)?|subcutaneous(?:ly)?|"
    r"s\.c\.|intraperitoneal(?:ly)?|i\.p\.|topical(?:ly)?|inhaled?)\b",
    re.IGNORECASE,
)
_TIMEPOINT = re.compile(
    r"\b(?:(?:at|after|before|during|within)\s+\d+(?:\.\d+)?\s*"
    r"(?:h|hr|hrs|hours?|days?|weeks?|months?|years?)|baseline|day\s+\d+|week\s+\d+)\b",
    re.IGNORECASE,
)
_POPULATION = re.compile(
    r"\b(?:healthy\s+(?:adults?|volunteers?|participants?|controls?)|"
    r"patients?\s+with\s+[A-Za-z][A-Za-z0-9 ,/\-]{2,35}?(?=\s+(?:were|was|received|showed|had|and)\b|[,.;)]|$)|"
    r"adults?|children|mice|rats?|rodents?|macaques?)\b",
    re.IGNORECASE,
)

_CONTEXT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("comparator", _COMPARATOR),
    ("comparator", _COMPARATOR_ALONE),
    ("dose", _DOSE),
    ("route", _ROUTE),
    ("timepoint", _TIMEPOINT),
    ("population", _POPULATION),
]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _as_float(text: str) -> float:
    return float(text.replace("−", "-").replace("–", "-").replace("—", "-"))


def _valid_effect(effect_type: str, value: float) -> bool:
    if not math.isfinite(value):
        return False
    if effect_type in RATIO_TYPES and value <= 0:
        return False
    return not (effect_type == "correlation" and not -1.0 <= value <= 1.0)


def primary_effect(quant: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the first recorded effect, if any."""
    if not isinstance(quant, dict):
        return None
    effects = quant.get("effects")
    if not isinstance(effects, list) or not effects:
        return None
    first = effects[0]
    return first if isinstance(first, dict) else None


def _effect_candidates(sentence: str) -> list[dict[str, Any]]:
    """Collect validated effect matches before attaching confidence intervals."""
    candidates: list[dict[str, Any]] = []
    for effect_type, pattern in _EFFECT_PATTERNS:
        for match in pattern.finditer(sentence):
            try:
                value = _as_float(match.group("value"))
            except ValueError:
                continue
            verb = str(match.groupdict().get("verb") or "").lower()
            if effect_type == "percent_change" and verb.startswith(("decreas", "reduc", "lower")):
                value = -abs(value)
            if not _valid_effect(effect_type, value):
                continue
            candidates.append(
                {
                    "type": effect_type,
                    "value": value,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    candidates.sort(key=lambda item: (int(item["start"]), int(item["end"]), str(item["type"])))
    return candidates


def _ci_after(sentence: str, start: int, stop: int) -> re.Match[str] | None:
    """Return the first CI wholly inside ``sentence[start:stop]``, if any."""
    window = sentence[start:stop]
    match = _CI.search(window)
    if match is None and re.match(r"\s*[\(\[]", window):
        match = _BRACKET_CI.search(window)
    return match


def extract_quant(sentence: str, absolute_start: int) -> dict[str, Any]:
    """Extract explicit effects and bounded context spans from one sentence.

    The returned offsets are absolute offsets into the source text, not offsets
    into ``sentence``. Every ``quote`` is the exact source slice it describes.
    A CI is attached only when it follows its effect and starts before the next
    explicit effect label, so two effects cannot silently share one interval.
    """
    effects: list[dict[str, Any]] = []
    candidates = _effect_candidates(sentence)
    seen: set[tuple[str, int, int, float]] = set()
    for index, candidate in enumerate(candidates):
        effect_type = str(candidate["type"])
        value = float(candidate["value"])
        start = int(candidate["start"])
        end = int(candidate["end"])
        next_start = int(candidates[index + 1]["start"]) if index + 1 < len(candidates) else len(sentence)
        window_end = min(end + 90, next_start, len(sentence))
        ci_low: float | None = None
        ci_high: float | None = None
        ci_match = _ci_after(sentence, end, window_end) if end < window_end else None
        if ci_match:
            try:
                low = _as_float(ci_match.group("low"))
                high = _as_float(ci_match.group("high"))
            except ValueError:
                low = high = 0.0
            if low <= high:
                ci_low, ci_high = low, high
                end += ci_match.end()
        key = (effect_type, start, end, value)
        if key in seen:
            continue
        seen.add(key)
        effects.append(
            {
                "type": effect_type,
                "value": value,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "unit": "",
                "quote": sentence[start:end],
                "start": absolute_start + start,
                "end": absolute_start + end,
            }
        )
    effects.sort(key=lambda item: (int(item["start"]), str(item["type"])))
    contexts: dict[str, dict[str, Any]] = {}
    for field, pattern in _CONTEXT_PATTERNS:
        if field in contexts:
            continue
        context_match = pattern.search(sentence)
        if not context_match:
            continue
        group = "text" if "text" in context_match.groupdict() else 0
        raw = context_match.group(group)
        if not raw:
            continue
        text = raw.strip()
        if not text:
            continue
        local_start = context_match.start(group) + (len(raw) - len(raw.lstrip()))
        contexts[field] = {
            "text": text,
            "start": absolute_start + local_start,
            "end": absolute_start + local_start + len(text),
        }
    payload: dict[str, Any] = {}
    if effects:
        payload["schema"] = QUANT_SCHEMA
        payload["effects"] = effects
        warnings: list[str] = []
        if len(effects) > 1:
            warnings.append("multiple_effects")
        if len({str(effect["type"]) for effect in effects}) > 1:
            warnings.append("multiple_effect_types")
        if warnings:
            payload["warnings"] = warnings
    if contexts:
        if not payload:
            payload["schema"] = QUANT_SCHEMA
        payload["contexts"] = contexts
    return payload


def effect_columns(quant: dict[str, Any] | None) -> dict[str, Any]:
    """Return the denormalized primary-effect columns for a claim row."""
    empty = {
        "effect_type": "",
        "effect_size": None,
        "effect_ci_low": None,
        "effect_ci_high": None,
    }
    primary = primary_effect(quant)
    if primary is None:
        return empty
    effect_type = str(primary.get("type") or "")
    value = primary.get("value")
    if effect_type not in EFFECT_TYPES or not _number(value):
        return empty
    low, high = primary.get("ci_low"), primary.get("ci_high")
    return {
        "effect_type": effect_type,
        "effect_size": float(str(value)),
        "effect_ci_low": float(str(low)) if _number(low) else None,
        "effect_ci_high": float(str(high)) if _number(high) else None,
    }


def validate_quant(sentence: str, absolute_start: int, quant: Any) -> list[str]:
    """Validate a stored ``quant`` object against its claim sentence."""
    errors: list[str] = []
    if not isinstance(quant, dict):
        return ["quant is not an object"]
    if not quant:
        return errors
    schema = quant.get("schema")
    if schema is not None and schema != QUANT_SCHEMA:
        errors.append("quant has an unknown schema")
    warnings = quant.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        errors.append("quant.warnings is not a list of strings")
    elif any(item not in QUANT_WARNINGS for item in warnings):
        errors.append("quant.warnings contains an unknown warning")
    effects = quant.get("effects", [])
    contexts = quant.get("contexts", {})
    if not isinstance(effects, list):
        errors.append("quant.effects is not a list")
        effects = []
    if not isinstance(contexts, dict):
        errors.append("quant.contexts is not an object")
        contexts = {}
    sentence_end = absolute_start + len(sentence)

    for index, effect in enumerate(effects):
        prefix = "effect[%d]" % index
        if not isinstance(effect, dict):
            errors.append(prefix + " is not an object")
            continue
        effect_type = effect.get("type")
        if effect_type not in EFFECT_TYPES:
            errors.append(prefix + " has an unknown type")
        value = effect.get("value")
        if not _number(value):
            errors.append(prefix + " has no finite value")
        elif isinstance(value, (int, float)):
            if effect_type in RATIO_TYPES and float(value) <= 0:
                errors.append(prefix + " has a non-positive ratio")
            if effect_type == "correlation" and not -1.0 <= float(value) <= 1.0:
                errors.append(prefix + " has a correlation outside [-1, 1]")
        for field in ("ci_low", "ci_high"):
            value = effect.get(field)
            if value is not None and not _number(value):
                errors.append(prefix + " has a non-finite " + field)
        low, high = effect.get("ci_low"), effect.get("ci_high")
        if _number(low) and _number(high):
            assert isinstance(low, (int, float)) and isinstance(high, (int, float))
            if float(low) > float(high):
                errors.append(prefix + " has an inverted CI")
        start, end = effect.get("start"), effect.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            errors.append(prefix + " has no integer span")
            continue
        if start < absolute_start or end > sentence_end or start >= end:
            errors.append(prefix + " span is outside the claim sentence")
            continue
        quote = effect.get("quote")
        expected = sentence[start - absolute_start : end - absolute_start]
        if not isinstance(quote, str) or quote != expected:
            errors.append(prefix + " quote does not match its span")

    for field, context in contexts.items():
        prefix = "context." + str(field)
        if field not in CONTEXT_FIELDS:
            errors.append(prefix + " is not a known context field")
            continue
        if not isinstance(context, dict):
            errors.append(prefix + " is not an object")
            continue
        start, end = context.get("start"), context.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            errors.append(prefix + " has no integer span")
            continue
        if start < absolute_start or end > sentence_end or start >= end:
            errors.append(prefix + " span is outside the claim sentence")
            continue
        text = context.get("text")
        expected = sentence[start - absolute_start : end - absolute_start]
        if not isinstance(text, str) or text != expected:
            errors.append(prefix + " text does not match its span")
    return errors


def format_effect(quant: dict[str, Any] | None) -> str:
    """Render the primary effect and context for human-readable reports."""
    primary = primary_effect(quant)
    if primary is None:
        return ""
    label = str(primary.get("type") or "effect").replace("_", " ")
    value = primary.get("value")
    text = "%s %s" % (label, value)
    low, high = primary.get("ci_low"), primary.get("ci_high")
    if _number(low) and _number(high):
        text += " [95%% CI %s, %s]" % (low, high)
    contexts = (quant or {}).get("contexts")
    if isinstance(contexts, dict):
        for field in ("comparator", "dose", "route", "timepoint", "population"):
            item = contexts.get(field)
            if isinstance(item, dict) and item.get("text"):
                text += "; %s: %s" % (field, item["text"])
    return text
