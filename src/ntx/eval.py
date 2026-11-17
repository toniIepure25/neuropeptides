"""Gold-set evaluation: mention detection, normalization, and claim tuples."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import annotation, stats
from .quant import primary_effect
from .store import Store

DOUBLE_ANNOTATION_MIN = 30


def load_gold(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


def _claim_effect(claim: dict[str, Any]) -> tuple[str, float | None, float | None, float | None]:
    effect_type = str(claim.get("effect_type") or "")
    value = claim.get("effect_size")
    low = claim.get("effect_ci_low")
    high = claim.get("effect_ci_high")
    if not effect_type:
        primary = primary_effect(claim.get("quant") if isinstance(claim.get("quant"), dict) else None)
        if primary:
            effect_type = str(primary.get("type") or "")
            value = primary.get("value")
            low = primary.get("ci_low")
            high = primary.get("ci_high")
    return (
        effect_type,
        float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None,
        float(low) if isinstance(low, (int, float)) and not isinstance(low, bool) else None,
        float(high) if isinstance(high, (int, float)) and not isinstance(high, bool) else None,
    )


def _row_effect(row: Any) -> tuple[str, float | None, float | None, float | None]:
    effect_type = str(row["effect_type"] or "")
    value = row["effect_size"]
    low = row["effect_ci_low"]
    high = row["effect_ci_high"]
    return (
        effect_type,
        float(value) if value is not None else None,
        float(low) if low is not None else None,
        float(high) if high is not None else None,
    )


def _effect_ci_overlap(
    left: tuple[str, float | None, float | None, float | None],
    right: tuple[str, float | None, float | None, float | None],
) -> bool | None:
    if left[2] is None or left[3] is None or right[2] is None or right[3] is None:
        return None
    return left[2] <= right[3] and right[2] <= left[3]


def gold_readiness(
    gold: list[dict[str, Any]],
    frame_documents: list[dict[str, Any]] | None = None,
    second_annotator: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Report whether the gold set is complete, human, and double-annotated.

    The model draft in ``gold/v2/draft.jsonl`` deliberately fails this gate. No
    evaluation metric should be described as human gold until every blocker is
    clear and adjudication has been recorded.
    """
    gold_docs = {str(record["doc_id"]) for record in gold}
    draft_docs = sorted(
        str(record["doc_id"]) for record in gold if str(record.get("status") or "").casefold() != "human"
    )
    second_docs = {
        str(record["doc_id"])
        for record in (second_annotator or [])
        if str(record.get("status") or "").casefold() == "human"
    }
    frame_ids = {str(doc["doc_id"]) for doc in (frame_documents or [])}
    missing = sorted(frame_ids - gold_docs) if frame_ids else []
    outside = sorted(gold_docs - frame_ids) if frame_ids else []
    blockers: list[str] = []
    if not gold:
        blockers.append("no_gold_records")
    if draft_docs:
        blockers.append("non_human_records")
    if frame_ids and missing:
        blockers.append("incomplete_frame")
    if len(second_docs) < DOUBLE_ANNOTATION_MIN:
        blockers.append("double_annotation_missing")
    if outside:
        blockers.append("documents_outside_frame")
    counts = {
        "gold_documents": len(gold_docs),
        "frame_documents": len(frame_ids) if frame_ids else None,
        "missing_documents": len(missing),
        "draft_documents": len(draft_docs),
        "double_annotated_documents": len(second_docs),
        "claims": sum(len(record.get("claims", [])) for record in gold),
        "effect_claims": sum(
            1 for record in gold for claim in record.get("claims", []) if _claim_effect(claim)[0]
        ),
    }
    return {
        "ready": not blockers,
        "blockers": blockers,
        "counts": counts,
        "missing_documents": missing[:50],
        "draft_documents": draft_docs[:50],
        "outside_frame": outside[:50],
    }


ClaimKey = tuple[str, str, str, str, int]


def _candidate_map(
    rows: list[tuple[str, str, str, str, int, str]],
) -> dict[tuple[str, str], list[tuple[int, str, str, str]]]:
    """(doc, entity) -> [(start, direction, outcome, role)]."""
    mapped: dict[tuple[str, str], list[tuple[int, str, str, str]]] = defaultdict(list)
    for doc_id, entity_id, direction, outcome, start, role in rows:
        mapped[(doc_id, entity_id)].append((start, direction, outcome, role))
    return mapped


def _classify_mismatch(
    key: ClaimKey,
    candidates: list[tuple[int, str, str, str]],
) -> str:
    """Attribute an unmatched claim to the nearest field-level disagreement."""
    doc_id, entity_id, direction, outcome, start = key
    if not candidates:
        return "entity_mismatch"
    same_sentence = [item for item in candidates if item[0] == start]
    if any(item[1] == direction for item in same_sentence):
        return "outcome_mismatch"
    if any(item[2] == outcome for item in same_sentence):
        return "direction_mismatch"
    if same_sentence:
        return "span_mismatch"
    return "sentence_mismatch"


def _error_breakdown(
    gold_keys: set[ClaimKey],
    pred_keys: set[ClaimKey],
    gold_rows: list[tuple[str, str, str, str, int, str]],
    pred_rows: list[tuple[str, str, str, str, int, str]],
    limit: int = 10,
) -> dict[str, Any]:
    gold_candidates = _candidate_map(gold_rows)
    pred_candidates = _candidate_map(pred_rows)
    result: dict[str, Any] = {
        "false_negatives": defaultdict(int),
        "false_positives": defaultdict(int),
        "examples": {"false_negatives": [], "false_positives": []},
    }
    for key in sorted(gold_keys - pred_keys):
        category = _classify_mismatch(key, pred_candidates.get((key[0], key[1]), []))
        result["false_negatives"][category] += 1
        if len(result["examples"]["false_negatives"]) < limit:
            result["examples"]["false_negatives"].append(
                {"doc_id": key[0], "entity_id": key[1], "start": key[4], "category": category}
            )
    for key in sorted(pred_keys - gold_keys):
        category = _classify_mismatch(key, gold_candidates.get((key[0], key[1]), []))
        result["false_positives"][category] += 1
        if len(result["examples"]["false_positives"]) < limit:
            result["examples"]["false_positives"].append(
                {"doc_id": key[0], "entity_id": key[1], "start": key[4], "category": category}
            )
    result["false_negatives"] = dict(sorted(result["false_negatives"].items()))
    result["false_positives"] = dict(sorted(result["false_positives"].items()))
    return result


def evaluate(store: Store, gold: list[dict[str, Any]]) -> dict[str, Any]:
    if any(annotation.is_redacted(item) for item in gold):
        raise ValueError("gold is redacted; run 'ntx gold hydrate' with a local corpus before evaluation")
    doc_ids = {item["doc_id"] for item in gold}
    texts: dict[str, str] = {}
    for row in store.conn.execute("SELECT doc_id, title, abstract FROM documents"):
        if row["doc_id"] in doc_ids:
            texts[row["doc_id"]] = (row["title"] or "") + "\n\n" + (row["abstract"] or "")
    gold_span_errors = 0
    gold_error_examples: list[dict[str, Any]] = []

    def record_gold_error(doc_id: str, kind: str, start: int) -> None:
        nonlocal gold_span_errors
        gold_span_errors += 1
        if len(gold_error_examples) < 10:
            gold_error_examples.append({"doc_id": doc_id, "kind": kind, "start": start})

    gold_spans: set[tuple[str, int, int]] = set()
    gold_ids: set[tuple[str, int, int, str]] = set()
    gold_claims: set[ClaimKey] = set()
    gold_role_claims: set[tuple[str, str, str, str, str, int]] = set()
    gold_rows: list[tuple[str, str, str, str, int, str]] = []
    gold_effects: dict[tuple[str, str, int], tuple[str, float | None, float | None, float | None]] = {}
    gold_effect_keys: set[tuple[str, str, str, str, int, str]] = set()
    gold_effect_claims = 0
    gold_with_role = 0
    for item in gold:
        doc_id = item["doc_id"]
        text = texts.get(doc_id)
        for mention in item.get("mentions", []):
            start = int(mention["start"])
            end = int(mention["end"])
            if text is not None and not (0 <= start < end <= len(text)):
                record_gold_error(doc_id, "mention_span", start)
            gold_spans.add((doc_id, start, end))
            gold_ids.add((doc_id, start, end, mention["entity_id"]))
        for claim in item.get("claims", []):
            entity_id = claim["entity_id"]
            direction = claim.get("direction", "unclear")
            outcome = claim.get("outcome", "other")
            start = int(claim.get("start", -1))
            sentence = str(claim.get("sentence") or "")
            role = str(claim.get("role") or "unknown")
            if text is not None:
                if not sentence:
                    record_gold_error(doc_id, "empty_sentence", start)
                elif (
                    start < 0
                    or start + len(sentence) > len(text)
                    or text[start : start + len(sentence)] != sentence
                ):
                    record_gold_error(doc_id, "sentence_span", start)
            if role not in {"", "unknown"}:
                gold_with_role += 1
            gold_claims.add((doc_id, entity_id, direction, outcome, start))
            gold_role_claims.add((doc_id, entity_id, direction, outcome, role, start))
            gold_rows.append((doc_id, entity_id, direction, outcome, start, role))
            effect = _claim_effect(claim)
            if effect[0]:
                gold_effect_claims += 1
                gold_effects[(doc_id, entity_id, start)] = effect
                gold_effect_keys.add((doc_id, entity_id, direction, outcome, start, effect[0]))

    pred_spans: set[tuple[str, int, int]] = set()
    pred_ids: set[tuple[str, int, int, str]] = set()
    pred_claims: set[ClaimKey] = set()
    pred_role_claims: set[tuple[str, str, str, str, str, int]] = set()
    pred_rows: list[tuple[str, str, str, str, int, str]] = []
    pred_effects: dict[tuple[str, str, int], tuple[str, float | None, float | None, float | None]] = {}
    pred_effect_keys: set[tuple[str, str, str, str, int, str]] = set()
    pred_effect_claims = 0
    for row in store.conn.execute("SELECT doc_id, entity_id, start, end FROM mentions WHERE accepted=1"):
        if row["doc_id"] in doc_ids:
            pred_spans.add((row["doc_id"], row["start"], row["end"]))
            pred_ids.add((row["doc_id"], row["start"], row["end"], row["entity_id"]))
    for row in store.conn.execute(
        "SELECT doc_id, entity_id, direction, outcome, start, role, effect_type, effect_size,"
        " effect_ci_low, effect_ci_high FROM claims"
    ):
        if row["doc_id"] in doc_ids:
            pred_claims.add((row["doc_id"], row["entity_id"], row["direction"], row["outcome"], row["start"]))
            pred_role_claims.add(
                (
                    row["doc_id"],
                    row["entity_id"],
                    row["direction"],
                    row["outcome"],
                    str(row["role"] or "unknown"),
                    row["start"],
                )
            )
            pred_rows.append(
                (
                    row["doc_id"],
                    row["entity_id"],
                    row["direction"],
                    row["outcome"],
                    row["start"],
                    str(row["role"] or "unknown"),
                )
            )
            effect = _row_effect(row)
            if effect[0]:
                pred_effect_claims += 1
                pred_effects[(row["doc_id"], row["entity_id"], row["start"])] = effect
                pred_effect_keys.add(
                    (
                        row["doc_id"],
                        row["entity_id"],
                        row["direction"],
                        row["outcome"],
                        row["start"],
                        effect[0],
                    )
                )

    span_tp = len(gold_spans & pred_spans)
    span_metrics = _prf(span_tp, len(pred_spans - gold_spans), len(gold_spans - pred_spans))

    id_tp = len(gold_ids & pred_ids)
    id_metrics = _prf(id_tp, len(pred_ids - gold_ids), len(gold_ids - pred_ids))

    # Normalization accuracy: of spans detected with the right boundaries, how
    # many carry the right identifier.
    matched = gold_spans & pred_spans
    correct = 0
    for doc_id, start, end in matched:
        gold_eids = {eid for (d, s, e, eid) in gold_ids if d == doc_id and s == start and e == end}
        pred_eids = {eid for (d, s, e, eid) in pred_ids if d == doc_id and s == start and e == end}
        if gold_eids & pred_eids:
            correct += 1
    id_accuracy = round(correct / len(matched), 3) if matched else 0.0

    claim_tp = len(gold_claims & pred_claims)
    claim_metrics = _prf(claim_tp, len(pred_claims - gold_claims), len(gold_claims - pred_claims))
    direction_tp = len(
        {(d, e, dir_) for (d, e, dir_, _o, _s) in gold_claims}
        & {(d, e, dir_) for (d, e, dir_, _o, _s) in pred_claims}
    )
    direction_metrics = _prf(
        direction_tp,
        len(
            {(d, e, dir_) for (d, e, dir_, _o, _s) in pred_claims}
            - {(d, e, dir_) for (d, e, dir_, _o, _s) in gold_claims}
        ),
        len(
            {(d, e, dir_) for (d, e, dir_, _o, _s) in gold_claims}
            - {(d, e, dir_) for (d, e, dir_, _o, _s) in pred_claims}
        ),
    )

    role_tp = len(gold_role_claims & pred_role_claims)
    role_metrics = _prf(
        role_tp, len(pred_role_claims - gold_role_claims), len(gold_role_claims - pred_role_claims)
    )
    matched_strict = gold_claims & pred_claims
    role_correct = 0
    role_mismatch_examples: list[dict[str, Any]] = []
    for key in sorted(matched_strict):
        gold_roles = {
            role for (d, e, dir_, out, role, st) in gold_role_claims if (d, e, dir_, out, st) == key
        }
        pred_roles = {
            role for (d, e, dir_, out, role, st) in pred_role_claims if (d, e, dir_, out, st) == key
        }
        if gold_roles & pred_roles:
            role_correct += 1
        elif len(role_mismatch_examples) < 10:
            role_mismatch_examples.append(
                {
                    "doc_id": key[0],
                    "entity_id": key[1],
                    "start": key[4],
                    "gold_roles": sorted(gold_roles),
                    "pred_roles": sorted(pred_roles),
                }
            )
    role_accuracy = round(role_correct / len(matched_strict), 3) if matched_strict else 0.0

    effect_tp = len(gold_effect_keys & pred_effect_keys)
    effect_metrics = _prf(
        effect_tp,
        len(pred_effect_keys - gold_effect_keys),
        len(gold_effect_keys - pred_effect_keys),
    )
    effect_type_correct = 0
    effect_type_compared = 0
    effect_value_errors: list[float] = []
    effect_value_errors_by_type: dict[str, list[float]] = defaultdict(list)
    effect_value_cross_type_pairs = 0
    effect_value_untyped_pairs = 0
    effect_ci_compared = 0
    effect_ci_overlapping = 0
    effect_mismatch_examples: list[dict[str, Any]] = []
    for key in sorted(matched_strict):
        gold_effect = gold_effects.get((key[0], key[1], key[4]))
        pred_effect = pred_effects.get((key[0], key[1], key[4]))
        if gold_effect is None or pred_effect is None:
            continue
        if gold_effect[0] and pred_effect[0]:
            effect_type_compared += 1
            if gold_effect[0] == pred_effect[0]:
                effect_type_correct += 1
            elif len(effect_mismatch_examples) < 10:
                effect_mismatch_examples.append(
                    {
                        "doc_id": key[0],
                        "entity_id": key[1],
                        "start": key[4],
                        "gold": gold_effect[0],
                        "pred": pred_effect[0],
                    }
                )
        if gold_effect[1] is not None and pred_effect[1] is not None:
            gold_type, pred_type = gold_effect[0], pred_effect[0]
            if gold_type and pred_type and gold_type == pred_type:
                error = abs(gold_effect[1] - pred_effect[1])
                effect_value_errors.append(error)
                effect_value_errors_by_type[gold_type].append(error)
            elif gold_type and pred_type:
                effect_value_cross_type_pairs += 1
            else:
                effect_value_untyped_pairs += 1
        overlap = _effect_ci_overlap(gold_effect, pred_effect)
        if overlap is not None:
            effect_ci_compared += 1
            effect_ci_overlapping += int(overlap)
    effect_type_accuracy = (
        round(effect_type_correct / effect_type_compared, 3) if effect_type_compared else 0.0
    )
    effect_value_mae = (
        round(sum(effect_value_errors) / len(effect_value_errors), 3) if effect_value_errors else None
    )
    effect_value_mae_by_type = {
        effect_type: {
            "n": len(errors),
            "mae": round(sum(errors) / len(errors), 3),
        }
        for effect_type, errors in sorted(effect_value_errors_by_type.items())
    }
    effect_ci_overlap_rate = (
        round(effect_ci_overlapping / effect_ci_compared, 3) if effect_ci_compared else None
    )

    integrity = store.provenance_errors()
    return {
        "documents": len(doc_ids),
        "mentions_span": span_metrics,
        "mentions_id": id_metrics,
        "normalization_accuracy_on_matched_spans": id_accuracy,
        "claims_strict": claim_metrics,
        "claims_direction_relaxed": direction_metrics,
        "claims_role_strict": role_metrics,
        "role_accuracy_on_matched_claims": role_accuracy,
        "role_gold_coverage": round(gold_with_role / len(gold_claims), 3) if gold_claims else 0.0,
        "claims_effect_strict": effect_metrics,
        "effect_type_accuracy_on_matched_claims": effect_type_accuracy,
        "effect_value_mae": effect_value_mae,
        "effect_value_mae_by_type": effect_value_mae_by_type,
        "effect_value_n": len(effect_value_errors),
        "effect_value_cross_type_pairs": effect_value_cross_type_pairs,
        "effect_value_untyped_pairs": effect_value_untyped_pairs,
        "effect_ci_overlap_rate": effect_ci_overlap_rate,
        "effect_ci_n": effect_ci_compared,
        "effect_gold_coverage": round(gold_effect_claims / len(gold_claims), 3) if gold_claims else 0.0,
        "effect_pred_coverage": round(pred_effect_claims / len(pred_claims), 3) if pred_claims else 0.0,
        "effect_mismatches": {
            "count": effect_type_compared - effect_type_correct,
            "examples": effect_mismatch_examples,
        },
        "role_mismatches": {
            "count": len(matched_strict) - role_correct,
            "examples": role_mismatch_examples,
        },
        "claim_error_breakdown": _error_breakdown(gold_claims, pred_claims, gold_rows, pred_rows),
        "provenance_errors": len(integrity),
        "gold_span_errors": gold_span_errors,
        "gold_missing_documents": len(doc_ids - set(texts)),
        "gold_error_examples": gold_error_examples,
        "gold_mentions": len(gold_spans),
        "pred_mentions": len(pred_spans),
        "gold_claims": len(gold_claims),
        "pred_claims": len(pred_claims),
    }


def format_report(result: dict[str, Any]) -> str:
    lines = ["Gold evaluation", "===============", ""]
    lines.append(
        "documents: %d | gold mentions: %d | predicted mentions: %d"
        % (result["documents"], result["gold_mentions"], result["pred_mentions"])
    )
    lines.append("gold claims: %d | predicted claims: %d" % (result["gold_claims"], result["pred_claims"]))
    lines.append("")
    for key in (
        "mentions_span",
        "mentions_id",
        "claims_strict",
        "claims_direction_relaxed",
        "claims_role_strict",
    ):
        m = result[key]
        lines.append("%-28s P=%.3f R=%.3f F1=%.3f" % (key, m["precision"], m["recall"], m["f1"]))
    lines.append("%-28s %.3f" % ("normalization accuracy", result["normalization_accuracy_on_matched_spans"]))
    lines.append(
        "%-28s %.3f (gold role coverage %.3f)"
        % (
            "role accuracy (matched)",
            result["role_accuracy_on_matched_claims"],
            result["role_gold_coverage"],
        )
    )
    lines.append("%-28s %d" % ("role mismatches on matched", result["role_mismatches"]["count"]))
    if result["effect_gold_coverage"]:
        effect = result["claims_effect_strict"]
        lines.append(
            "%-28s P=%.3f R=%.3f F1=%.3f"
            % ("claims_effect_strict", effect["precision"], effect["recall"], effect["f1"])
        )
        lines.append(
            "%-28s %.3f (gold coverage %.3f)"
            % (
                "effect type accuracy",
                result["effect_type_accuracy_on_matched_claims"],
                result["effect_gold_coverage"],
            )
        )
        if result["effect_value_mae"] is not None:
            lines.append(
                "%-28s %.3f (n=%d, same type only)"
                % ("effect value MAE", result["effect_value_mae"], result["effect_value_n"])
            )
        for effect_type, metric in result["effect_value_mae_by_type"].items():
            lines.append("%-28s %.3f (n=%d)" % ("  MAE " + effect_type, metric["mae"], metric["n"]))
        if result["effect_value_cross_type_pairs"]:
            lines.append(
                "%-28s %d (excluded from MAE)"
                % ("effect cross-type pairs", result["effect_value_cross_type_pairs"])
            )
        if result["effect_ci_overlap_rate"] is not None:
            lines.append(
                "%-28s %.3f (n=%d)"
                % ("effect CI overlap rate", result["effect_ci_overlap_rate"], result["effect_ci_n"])
            )
    lines.append("%-28s %d" % ("provenance errors", result["provenance_errors"]))
    lines.append("%-28s %d" % ("gold span errors", result["gold_span_errors"]))
    if result["gold_missing_documents"]:
        lines.append("%-28s %d" % ("gold docs missing from db", result["gold_missing_documents"]))
    breakdown = result["claim_error_breakdown"]
    for side, label in (("false_negatives", "FN"), ("false_positives", "FP")):
        if breakdown[side]:
            parts = ", ".join("%s=%d" % item for item in breakdown[side].items())
            lines.append("%-28s %s" % ("claim " + label + " by cause", parts))
    return "\n".join(lines)


def document_counts(store: Store, gold: list[dict[str, Any]]) -> dict[str, tuple[int, int, int]]:
    """Per-document ``(tp, fp, fn)`` for strict claim matching.

    Counting per document (rather than per claim) preserves clustering for the
    bootstrap: sentences from one abstract are not independent samples.
    """
    doc_ids = {item["doc_id"] for item in gold}
    gold_claims: dict[str, set[tuple[str, str, str, int]]] = defaultdict(set)
    for item in gold:
        for claim in item.get("claims", []):
            gold_claims[item["doc_id"]].add(
                (
                    claim["entity_id"],
                    claim.get("direction", "unclear"),
                    claim.get("outcome", "other"),
                    int(claim.get("start", -1)),
                )
            )
    pred_claims: dict[str, set[tuple[str, str, str, int]]] = defaultdict(set)
    for row in store.conn.execute("SELECT doc_id, entity_id, direction, outcome, start FROM claims"):
        if row["doc_id"] in doc_ids:
            pred_claims[row["doc_id"]].add((row["entity_id"], row["direction"], row["outcome"], row["start"]))

    counts: dict[str, tuple[int, int, int]] = {}
    for doc_id in sorted(doc_ids):
        gold_set = gold_claims.get(doc_id, set())
        pred_set = pred_claims.get(doc_id, set())
        counts[doc_id] = (len(gold_set & pred_set), len(pred_set - gold_set), len(gold_set - pred_set))
    return counts


def document_counts_role(store: Store, gold: list[dict[str, Any]]) -> dict[str, tuple[int, int, int]]:
    """Per-document ``(tp, fp, fn)`` for role-strict claim matching."""
    doc_ids = {item["doc_id"] for item in gold}
    gold_claims: dict[str, set[tuple[str, str, str, str, int]]] = defaultdict(set)
    for item in gold:
        for claim in item.get("claims", []):
            gold_claims[item["doc_id"]].add(
                (
                    claim["entity_id"],
                    claim.get("direction", "unclear"),
                    claim.get("outcome", "other"),
                    str(claim.get("role") or "unknown"),
                    int(claim.get("start", -1)),
                )
            )
    pred_claims: dict[str, set[tuple[str, str, str, str, int]]] = defaultdict(set)
    for row in store.conn.execute("SELECT doc_id, entity_id, direction, outcome, role, start FROM claims"):
        if row["doc_id"] in doc_ids:
            pred_claims[row["doc_id"]].add(
                (
                    row["entity_id"],
                    row["direction"],
                    row["outcome"],
                    str(row["role"] or "unknown"),
                    row["start"],
                )
            )
    counts: dict[str, tuple[int, int, int]] = {}
    for doc_id in sorted(doc_ids):
        gold_set = gold_claims.get(doc_id, set())
        pred_set = pred_claims.get(doc_id, set())
        counts[doc_id] = (len(gold_set & pred_set), len(pred_set - gold_set), len(gold_set - pred_set))
    return counts


def document_counts_effect(store: Store, gold: list[dict[str, Any]]) -> dict[str, tuple[int, int, int]]:
    """Per-document ``(tp, fp, fn)`` for effect-type-strict claim matching."""
    doc_ids = {item["doc_id"] for item in gold}
    gold_claims: dict[str, set[tuple[str, str, str, str, int]]] = defaultdict(set)
    for item in gold:
        for claim in item.get("claims", []):
            effect = _claim_effect(claim)
            if effect[0]:
                gold_claims[item["doc_id"]].add(
                    (
                        claim["entity_id"],
                        claim.get("direction", "unclear"),
                        claim.get("outcome", "other"),
                        effect[0],
                        int(claim.get("start", -1)),
                    )
                )
    pred_claims: dict[str, set[tuple[str, str, str, str, int]]] = defaultdict(set)
    for row in store.conn.execute(
        "SELECT doc_id, entity_id, direction, outcome, effect_type, start FROM claims"
    ):
        if row["doc_id"] in doc_ids and row["effect_type"]:
            pred_claims[row["doc_id"]].add(
                (
                    row["entity_id"],
                    row["direction"],
                    row["outcome"],
                    row["effect_type"],
                    row["start"],
                )
            )
    counts: dict[str, tuple[int, int, int]] = {}
    for doc_id in sorted(doc_ids):
        gold_set = gold_claims.get(doc_id, set())
        pred_set = pred_claims.get(doc_id, set())
        counts[doc_id] = (len(gold_set & pred_set), len(pred_set - gold_set), len(gold_set - pred_set))
    return counts


def evaluate_with_ci(
    store: Store,
    gold: list[dict[str, Any]],
    strata: dict[str, str] | None = None,
    n_boot: int = 2000,
    seed: int = 0,
) -> dict[str, Any]:
    """Aggregate metrics plus bootstrap 95% CIs and optional per-stratum breakdown."""
    aggregate = evaluate(store, gold)
    counts = document_counts(store, gold)
    rows = [counts[doc_id] for doc_id in counts]
    result: dict[str, Any] = {
        "aggregate": aggregate,
        "claims_ci": stats.bootstrap_prf(rows, n_boot=n_boot, seed=seed),
        "documents": len(counts),
    }
    role_counts = document_counts_role(store, gold)
    if aggregate["role_gold_coverage"] > 0:
        result["claims_role_ci"] = stats.bootstrap_prf(
            [role_counts[doc_id] for doc_id in role_counts], n_boot=n_boot, seed=seed
        )
    effect_counts = document_counts_effect(store, gold)
    if aggregate["effect_gold_coverage"] > 0:
        result["claims_effect_ci"] = stats.bootstrap_prf(
            [effect_counts[doc_id] for doc_id in effect_counts], n_boot=n_boot, seed=seed
        )
    if strata:
        grouped: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        role_grouped: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        effect_grouped: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        for doc_id, row in counts.items():
            grouped[strata.get(doc_id, "unknown")].append(row)
            role_grouped[strata.get(doc_id, "unknown")].append(role_counts[doc_id])
            effect_grouped[strata.get(doc_id, "unknown")].append(effect_counts[doc_id])
        by_stratum: dict[str, Any] = {}
        for name, group_rows in sorted(grouped.items()):
            tp = sum(row[0] for row in group_rows)
            fp = sum(row[1] for row in group_rows)
            fn = sum(row[2] for row in group_rows)
            precision, recall, f1 = stats.prf(tp, fp, fn)
            entry: dict[str, Any] = {
                "documents": len(group_rows),
                "gold_claims": tp + fn,
                "pred_claims": tp + fp,
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
                "ci": stats.bootstrap_prf(group_rows, n_boot=n_boot, seed=seed),
            }
            if aggregate["role_gold_coverage"] > 0:
                role_tp = sum(row[0] for row in role_grouped[name])
                role_fp = sum(row[1] for row in role_grouped[name])
                role_fn = sum(row[2] for row in role_grouped[name])
                entry["claims_role_strict"] = _prf(role_tp, role_fp, role_fn)
            if aggregate["effect_gold_coverage"] > 0:
                effect_tp = sum(row[0] for row in effect_grouped[name])
                effect_fp = sum(row[1] for row in effect_grouped[name])
                effect_fn = sum(row[2] for row in effect_grouped[name])
                entry["claims_effect_strict"] = _prf(effect_tp, effect_fp, effect_fn)
            by_stratum[name] = entry
        result["by_stratum"] = by_stratum
    return result


def format_ci_report(result: dict[str, Any]) -> str:
    lines = [format_report(result["aggregate"]), ""]
    lines.append("Claim F1 with 95% bootstrap CI (documents resampled)")
    for name, values in result["claims_ci"].items():
        lines.append("%-10s %.3f  [%.3f, %.3f]" % (name, values["value"], values["low"], values["high"]))
    for name, values in result.get("claims_role_ci", {}).items():
        lines.append("role %-5s %.3f  [%.3f, %.3f]" % (name, values["value"], values["low"], values["high"]))
    for name, values in result.get("claims_effect_ci", {}).items():
        lines.append(
            "effect %-4s %.3f  [%.3f, %.3f]" % (name, values["value"], values["low"], values["high"])
        )
    for stratum, payload in result.get("by_stratum", {}).items():
        lines.append("")
        lines.append(
            "stratum %s: docs=%d gold=%d pred=%d F1=%.3f [%.3f, %.3f]"
            % (
                stratum,
                payload["documents"],
                payload["gold_claims"],
                payload["pred_claims"],
                payload["f1"],
                payload["ci"]["f1"]["low"],
                payload["ci"]["f1"]["high"],
            )
        )
    return "\n".join(lines)
