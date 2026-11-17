"""Gold annotation schema v2: validation, agreement inputs, and adjudication.

The gold record is intentionally close to the extracted claim so that a human
annotation and a system prediction can be compared field by field. Records are
JSONL, one document per line, with an explicit ``schema_version``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import quant, stats

SCHEMA_VERSION = 2
DIRECTIONS = {"increase", "decrease", "none", "mixed", "unclear"}
OUTCOMES = {
    "cognition",
    "memory",
    "attention",
    "social_cognition",
    "anxiety",
    "depression",
    "feeding",
    "pain",
    "neuroplasticity",
    "neuroprotection",
    "other",
}
ROLES = {"intervention", "comparator", "mechanism", "unknown"}
DESIGNS = {
    "meta_analysis",
    "systematic_review",
    "rct",
    "trial",
    "cohort",
    "case_control",
    "cross_sectional",
    "animal",
    "in_vitro",
    "review",
    "unknown",
}
MENTION_FORMS = {"peptide", "gene", "acronym", "brand", "other"}
REDACTED_HASH_FIELDS = ("sentence_sha256", "surface_sha256")


def sha256_text(value: str) -> str:
    """Return the lowercase SHA-256 of UTF-8 text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def is_redacted(record: dict[str, Any]) -> bool:
    """A record is redacted when its verbatim text is replaced by hashes."""
    if record.get("redacted"):
        return True
    return any("sentence" not in claim and "sentence_sha256" in claim for claim in record.get("claims", []))


def redact_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with verbatim third-party text replaced by SHA-256 hashes."""
    clean = json.loads(json.dumps(record))
    clean.pop("_line", None)
    clean["redacted"] = True
    for mention in clean.get("mentions", []):
        surface = mention.pop("surface", None)
        if isinstance(surface, str):
            mention["surface_sha256"] = sha256_text(surface)
    for claim in clean.get("claims", []):
        sentence = claim.pop("sentence", None)
        if isinstance(sentence, str):
            claim["sentence_sha256"] = sha256_text(sentence)
        # Quant payloads contain exact quote/context text; explicit effect fields
        # remain available without redistributing the source sentence.
        claim.pop("quant", None)
    return clean


def hydrate_record(record: dict[str, Any], text: str) -> dict[str, Any]:
    """Restore sentence/surface text from a local source and verify every hash."""
    hydrated = json.loads(json.dumps(record))
    hydrated.pop("_line", None)
    doc_id = str(hydrated.get("doc_id") or "<unknown>")
    for index, mention in enumerate(hydrated.get("mentions", [])):
        start, end = mention.get("start"), mention.get("end")
        if not _require_int(start) or not _require_int(end) or not 0 <= start < end <= len(text):
            raise ValueError("%s mentions[%d]: span outside corpus text" % (doc_id, index))
        surface = text[start:end]
        expected = mention.pop("surface_sha256", None)
        if expected is not None:
            if not _valid_sha256(expected):
                raise ValueError("%s mentions[%d]: invalid surface_sha256" % (doc_id, index))
            if sha256_text(surface) != expected:
                raise ValueError("%s mentions[%d]: surface hash mismatch" % (doc_id, index))
        mention["surface"] = surface
    for index, claim in enumerate(hydrated.get("claims", [])):
        start, end = claim.get("start"), claim.get("end")
        if not _require_int(start) or not _require_int(end) or not 0 <= start < end <= len(text):
            raise ValueError("%s claims[%d]: span outside corpus text" % (doc_id, index))
        sentence = text[start:end]
        expected = claim.pop("sentence_sha256", None)
        if expected is not None:
            if not _valid_sha256(expected):
                raise ValueError("%s claims[%d]: invalid sentence_sha256" % (doc_id, index))
            if sha256_text(sentence) != expected:
                raise ValueError("%s claims[%d]: sentence hash mismatch" % (doc_id, index))
        claim["sentence"] = sentence
    hydrated.pop("redacted", None)
    return hydrated


def _require_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def validate_record(record: dict[str, Any], text: str | None = None) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors: list[str] = []
    doc_id = record.get("doc_id")
    if not doc_id or not isinstance(doc_id, str):
        errors.append("missing doc_id")
        doc_id = "<unknown>"
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append("%s: schema_version must be %d" % (doc_id, SCHEMA_VERSION))

    mentions = record.get("mentions", [])
    if not isinstance(mentions, list):
        errors.append("%s: mentions must be a list" % doc_id)
        mentions = []
    seen_mentions: set[tuple[str, int, int]] = set()
    for index, mention in enumerate(mentions):
        where = "%s mentions[%d]" % (doc_id, index)
        if not mention.get("entity_id"):
            errors.append(where + ": missing entity_id")
        start, end = mention.get("start"), mention.get("end")
        if not _require_int(start) or not _require_int(end):
            errors.append(where + ": start/end must be integers")
            continue
        if not 0 <= start < end:
            errors.append(where + ": requires 0 <= start < end")
            continue
        if text is not None and end > len(text):
            errors.append(where + ": span exceeds text length")
        surface = mention.get("surface")
        surface_hash = mention.get("surface_sha256")
        if surface_hash is not None and not _valid_sha256(surface_hash):
            errors.append(where + ": surface_sha256 must be 64 lowercase hex characters")
        if surface is not None and surface_hash is not None and sha256_text(str(surface)) != surface_hash:
            errors.append(where + ": surface_sha256 does not match surface")
        if text is not None:
            if surface is None:
                errors.append(where + ": missing surface (hydrate redacted gold first)")
            elif text[start:end] != surface:
                errors.append(where + ": surface does not match text[start:end]")
        elif surface is None and surface_hash is None:
            errors.append(where + ": missing surface or surface_sha256")
        if start < 0:
            errors.append(where + ": negative offset")
        key = (str(mention.get("entity_id")), start, end)
        if key in seen_mentions:
            errors.append(where + ": duplicate mention span")
        seen_mentions.add(key)
        form = mention.get("mention_form")
        if form is not None and form not in MENTION_FORMS:
            errors.append(where + ": unknown mention_form %r" % form)

    claims = record.get("claims", [])
    if not isinstance(claims, list):
        errors.append("%s: claims must be a list" % doc_id)
        claims = []
    seen_claims: set[tuple[str, int, int, str, str]] = set()
    for index, claim in enumerate(claims):
        where = "%s claims[%d]" % (doc_id, index)
        entity_id = claim.get("entity_id")
        if not entity_id:
            errors.append(where + ": missing entity_id")
        start, end = claim.get("start"), claim.get("end")
        if not _require_int(start) or not _require_int(end):
            errors.append(where + ": start/end must be integers")
            continue
        if not 0 <= start < end:
            errors.append(where + ": requires 0 <= start < end")
            continue
        if text is not None and end > len(text):
            errors.append(where + ": span exceeds text length")
        sentence = claim.get("sentence")
        sentence_hash = claim.get("sentence_sha256")
        if sentence_hash is not None and not _valid_sha256(sentence_hash):
            errors.append(where + ": sentence_sha256 must be 64 lowercase hex characters")
        if sentence is not None and sentence_hash is not None and sha256_text(str(sentence)) != sentence_hash:
            errors.append(where + ": sentence_sha256 does not match sentence")
        if text is not None:
            if not sentence:
                errors.append(where + ": missing sentence (hydrate redacted gold first)")
            elif text[start:end] != sentence:
                errors.append(where + ": sentence does not match text[start:end]")
        elif not sentence and not sentence_hash:
            errors.append(where + ": missing sentence or sentence_sha256")
        direction = claim.get("direction")
        if direction not in DIRECTIONS:
            errors.append(where + ": unknown direction %r" % direction)
        outcome = claim.get("outcome")
        if outcome not in OUTCOMES:
            errors.append(where + ": unknown outcome %r" % outcome)
        role = claim.get("role", "unknown")
        if role not in ROLES:
            errors.append(where + ": unknown role %r" % role)
        design = claim.get("design")
        if design is not None and design not in DESIGNS:
            errors.append(where + ": unknown design %r" % design)
        effect_type = claim.get("effect_type", "")
        if effect_type and effect_type not in quant.EFFECT_TYPES:
            errors.append(where + ": unknown effect_type %r" % effect_type)
        effect_size = claim.get("effect_size")
        if effect_size is not None:
            if not effect_type:
                errors.append(where + ": effect_size requires effect_type")
            if not _finite_number(effect_size):
                errors.append(where + ": effect_size must be a finite number")
        low, high = claim.get("effect_ci_low"), claim.get("effect_ci_high")
        if low is not None or high is not None:
            if effect_size is None:
                errors.append(where + ": effect CI requires effect_size")
            if not _finite_number(low) or not _finite_number(high):
                errors.append(where + ": effect CI bounds must be finite numbers")
            elif float(low) > float(high):
                errors.append(where + ": effect CI is inverted")
        if claim.get("quant") is not None:
            if not isinstance(claim["quant"], dict):
                errors.append(where + ": quant must be an object")
            elif sentence:
                for message in quant.validate_quant(str(sentence), start, claim["quant"]):
                    errors.append(where + ": quant: " + message)
        claim_key = (str(entity_id), start, end, str(direction), str(outcome))
        if claim_key in seen_claims:
            errors.append(where + ": duplicate claim")
        seen_claims.add(claim_key)
    return errors


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            record["_line"] = line_number
            records.append(record)
    return records


def labels_by_key(
    records: Iterable[dict[str, Any]], field: str = "direction"
) -> dict[tuple[str, str, int], set[str]]:
    """Map (doc, entity, sentinel start) to the set of labels assigned.

    Multiple claims on the same entity and sentence are joined as a set, so a
    split into two findings versus one shows up as disagreement rather than
    being silently dropped.
    """
    labels: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for record in records:
        doc_id = record["doc_id"]
        for claim in record.get("claims", []):
            key = (doc_id, claim["entity_id"], int(claim["start"]))
            labels[key].add(str(claim.get(field, "")))
    return dict(labels)


def agreement_pairs(
    left: Iterable[dict[str, Any]], right: Iterable[dict[str, Any]], field: str = "direction"
) -> list[tuple[str, str]]:
    """Aligned label pairs on the keys both annotators annotated."""
    left_map = labels_by_key(left, field)
    right_map = labels_by_key(right, field)
    pairs: list[tuple[str, str]] = []
    for key in sorted(set(left_map) & set(right_map)):
        pairs.append(("+".join(sorted(left_map[key])), "+".join(sorted(right_map[key]))))
    return pairs


def _claim_effect(claim: dict[str, Any]) -> dict[str, Any] | None:
    effect_type = str(claim.get("effect_type") or "")
    value = claim.get("effect_size")
    if effect_type and isinstance(value, (int, float)) and not isinstance(value, bool):
        return {
            "type": effect_type,
            "value": float(value),
            "ci_low": claim.get("effect_ci_low"),
            "ci_high": claim.get("effect_ci_high"),
        }
    primary = quant.primary_effect(claim.get("quant"))
    if primary is None:
        return None
    return {
        "type": str(primary.get("type") or ""),
        "value": primary.get("value"),
        "ci_low": primary.get("ci_low"),
        "ci_high": primary.get("ci_high"),
    }


def effect_pairs(left: Iterable[dict[str, Any]], right: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return aligned primary-effect pairs for claims both annotators reviewed."""
    left_map: dict[tuple[str, str, int], dict[str, Any]] = {}
    right_map: dict[tuple[str, str, int], dict[str, Any]] = {}
    for records, target in ((left, left_map), (right, right_map)):
        for record in records:
            doc_id = record["doc_id"]
            for claim in record.get("claims", []):
                key = (doc_id, claim["entity_id"], int(claim["start"]))
                effect = _claim_effect(claim)
                if effect is not None and key not in target:
                    target[key] = effect
    pairs: list[dict[str, Any]] = []
    for key in sorted(set(left_map) & set(right_map)):
        pairs.append(
            {
                "doc_id": key[0],
                "entity_id": key[1],
                "start": key[2],
                "left": left_map[key],
                "right": right_map[key],
            }
        )
    return pairs


def effect_agreement(left: Iterable[dict[str, Any]], right: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Agreement on effect type, value and CI where both annotators supplied one."""
    pairs = effect_pairs(left, right)
    type_pairs = [(str(pair["left"]["type"]), str(pair["right"]["type"])) for pair in pairs]
    value_errors: list[float] = []
    ci_compared = 0
    ci_overlapping = 0
    examples: list[dict[str, Any]] = []
    for pair in pairs:
        left_effect = pair["left"]
        right_effect = pair["right"]
        if left_effect["type"] and left_effect["type"] == right_effect["type"]:
            left_value = left_effect.get("value")
            right_value = right_effect.get("value")
            if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
                value_errors.append(abs(float(left_value) - float(right_value)))
        elif len(examples) < 10:
            examples.append(pair)
        left_low, left_high = left_effect.get("ci_low"), left_effect.get("ci_high")
        right_low, right_high = right_effect.get("ci_low"), right_effect.get("ci_high")
        if all(isinstance(value, (int, float)) for value in (left_low, left_high, right_low, right_high)):
            ci_compared += 1
            ci_overlapping += int(
                float(left_low) <= float(right_high) and float(right_low) <= float(left_high)
            )
    return {
        "compared_items": len(pairs),
        "effect_type": stats.cohen_kappa(type_pairs),
        "effect_value_n": len(value_errors),
        "effect_value_mae": round(sum(value_errors) / len(value_errors), 3) if value_errors else None,
        "effect_ci_n": ci_compared,
        "effect_ci_overlap_rate": round(ci_overlapping / ci_compared, 3) if ci_compared else None,
        "effect_type_mismatch_examples": examples,
    }


def apply_adjudications(
    records: list[dict[str, Any]], adjudications: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Override claim fields using explicit adjudication decisions.

    Each adjudication is ``{doc_id, entity_id, start, field, value}``. Unknown
    keys are ignored (reported by the caller via a dry-run validate).
    """
    overrides: dict[tuple[str, str, int], dict[str, Any]] = defaultdict(dict)
    for item in adjudications:
        value = item.get("value")
        if value in (None, ""):
            continue  # an empty row is an unfilled conflict, not a decision
        key = (item["doc_id"], item["entity_id"], int(item["start"]))
        overrides[key][item["field"]] = value
    for record in records:
        for claim in record.get("claims", []):
            key = (record["doc_id"], claim["entity_id"], int(claim["start"]))
            if key in overrides:
                claim.update(overrides[key])
    return records


def merge_records(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union two annotation sets, preferring ``primary`` on identical claims."""
    by_doc = {record["doc_id"]: record for record in primary}
    for record in secondary:
        target = by_doc.setdefault(
            record["doc_id"],
            {"schema_version": SCHEMA_VERSION, "doc_id": record["doc_id"], "mentions": [], "claims": []},
        )
        seen_mentions = {(m["entity_id"], m["start"], m["end"]) for m in target.get("mentions", [])}
        for mention in record.get("mentions", []):
            mention_key = (mention["entity_id"], mention["start"], mention["end"])
            if mention_key not in seen_mentions:
                target.setdefault("mentions", []).append(mention)
                seen_mentions.add(mention_key)
        seen_claims = {
            (c["entity_id"], c["start"], c["end"], c["direction"], c["outcome"])
            for c in target.get("claims", [])
        }
        for claim in record.get("claims", []):
            claim_key = (
                claim["entity_id"],
                claim["start"],
                claim["end"],
                claim["direction"],
                claim["outcome"],
            )
            if claim_key not in seen_claims:
                target.setdefault("claims", []).append(claim)
                seen_claims.add(claim_key)
    return [by_doc[doc_id] for doc_id in sorted(by_doc)]


def write_records(records: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            clean = {key: value for key, value in record.items() if not key.startswith("_")}
            handle.write(json.dumps(clean, sort_keys=True) + "\n")
