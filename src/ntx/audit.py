"""Deterministic manual-audit sampling.

Without a human gold set, the honest way to estimate claim precision is a small
audit of model output. This module draws a reproducible, role/design-stratified
sample and renders a worksheet; it never computes precision itself, because that
requires a human verdict.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import quant as quant_mod
from . import stats
from .store import Store

VERDICTS = ("support", "refute", "unclear")
EFFECT_VERDICTS = ("correct", "incorrect", "unclear")


def _text(title: str, abstract: str) -> str:
    return (title or "") + "\n\n" + (abstract or "")


def sample_claims(
    store: Store, n: int = 40, seed: int = 0, *, effects_only: bool = False
) -> list[dict[str, Any]]:
    """Draw at most ``n`` claims, stratified by effect/role/design when requested."""
    if n < 1:
        raise ValueError("n must be at least 1")
    where = " WHERE c.effect_type != ''" if effects_only else ""
    rows = list(
        store.conn.execute(
            "SELECT c.claim_id, c.doc_id, c.entity_id, c.direction, c.outcome, c.role,"
            " c.design, c.section, c.sentence, c.start, c.end, c.evidence_start,"
            " c.evidence_end, c.effect_type, c.effect_size, c.effect_ci_low,"
            " c.effect_ci_high, c.quant, d.title, d.abstract"
            " FROM claims c JOIN documents d ON d.doc_id = c.doc_id" + where + " ORDER BY c.claim_id"
        )
    )
    if not rows:
        return []

    groups: dict[tuple[str, ...], list[Any]] = defaultdict(list)
    for row in rows:
        key: tuple[str, ...]
        if effects_only:
            key = (
                str(row["effect_type"] or "unknown"),
                str(row["role"] or "unknown"),
                str(row["design"] or "unknown"),
            )
        else:
            key = (str(row["role"] or "unknown"), str(row["design"] or "unknown"))
        groups[key].append(row)

    rng = random.Random(seed)
    names = sorted(groups, key=lambda key: (-len(groups[key]), key))
    for name in names:
        rng.shuffle(groups[name])

    if n <= len(names):
        chosen = dict.fromkeys(names[:n], 1)
    else:
        total = len(rows)
        quotas = {name: n * len(groups[name]) / total for name in names}
        chosen = {name: int(quotas[name]) for name in names}
        remainder = n - sum(chosen.values())
        ranked = sorted(names, key=lambda name: (-(quotas[name] - chosen[name]), name))
        for name in ranked[:remainder]:
            chosen[name] += 1

    picked: list[Any] = []
    for name in names:
        picked.extend(groups[name][: chosen.get(name, 0)])
    picked.sort(key=lambda row: (row["doc_id"], row["start"], row["claim_id"]))

    result: list[dict[str, Any]] = []
    for row in picked:
        text = _text(row["title"], row["abstract"])
        evidence = ""
        if row["evidence_start"] is not None and row["evidence_end"] is not None:
            evidence = text[row["evidence_start"] : row["evidence_end"]]
        try:
            quant = json.loads(row["quant"]) if row["quant"] else {}
        except (TypeError, json.JSONDecodeError):
            quant = {}
        result.append(
            {
                "claim_id": row["claim_id"],
                "doc_id": row["doc_id"],
                "entity_id": row["entity_id"],
                "direction": row["direction"],
                "outcome": row["outcome"],
                "role": row["role"],
                "design": row["design"],
                "section": row["section"],
                "sentence": row["sentence"],
                "evidence": evidence,
                "start": row["start"],
                "end": row["end"],
                "effect_type": row["effect_type"] or "",
                "effect_size": row["effect_size"],
                "effect_ci_low": row["effect_ci_low"],
                "effect_ci_high": row["effect_ci_high"],
                "quant": quant if isinstance(quant, dict) else {},
            }
        )
    return result


def format_worksheet(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Claim audit worksheet",
        "",
        "Deterministic sample of model claims. Mark one verdict per claim; do not",
        "edit the claim text. `support` = the sentence supports the extracted",
        "direction/outcome/role; `refute` = it does not; `unclear` = ambiguous.",
        "",
        "| # | Doc | Entity | Direction / outcome | Effect | Role | Section | Verdict |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, start=1):
        effect_text = quant_mod.format_effect(row.get("quant"))
        if not effect_text and row.get("effect_type"):
            effect_text = "%s %s" % (row["effect_type"], row.get("effect_size"))
        effect_text = effect_text or "—"
        lines.append(
            "| %d | %s | %s | %s / %s | %s | %s | %s | support / refute / unclear |"
            % (
                index,
                row["doc_id"],
                row["entity_id"],
                row["direction"],
                row["outcome"],
                effect_text,
                row["role"],
                row["section"],
            )
        )
    lines.append("")
    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                "## %d. %s · %s" % (index, row["doc_id"], row["entity_id"]),
                "",
                "- direction/outcome: `%s` / `%s`" % (row["direction"], row["outcome"]),
                "- role/section: `%s` / `%s` · design: `%s`" % (row["role"], row["section"], row["design"]),
                '- sentence: "%s"' % row["sentence"].replace('"', "'"),
            ]
        )
        effect_text = quant_mod.format_effect(row.get("quant"))
        if not effect_text and row.get("effect_type"):
            effect_text = "%s %s" % (row["effect_type"], row.get("effect_size"))
        if effect_text:
            lines.append("- effect: `%s`" % effect_text)
        if row["evidence"]:
            lines.append('- evidence: "%s"' % row["evidence"].replace('"', "'"))
        lines.append("- verdict: [ ] support  [ ] refute  [ ] unclear")
        if row.get("effect_type"):
            lines.append("- effect verdict: [ ] correct  [ ] incorrect  [ ] unclear")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_verdict_template(rows: list[dict[str, Any]]) -> str:
    """Machine-readable verdict skeleton, one row per sampled claim."""
    lines = []
    for row in rows:
        lines.append(
            json.dumps(
                {
                    "claim_id": row["claim_id"],
                    "doc_id": row["doc_id"],
                    "entity_id": row["entity_id"],
                    "start": row["start"],
                    "verdict": "",
                    "effect_verdict": "",
                    "effect_notes": "",
                },
                sort_keys=True,
            )
        )
    return "\n".join(lines) + ("\n" if lines else "")


def load_verdicts(path: Path) -> list[dict[str, Any]]:
    """Read a verdict JSONL file, rejecting malformed or duplicate decisions."""
    verdicts: list[dict[str, Any]] = []
    seen: set[int] = set()
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("%s:%d: invalid JSON: %s" % (path, line_number, exc)) from exc
            if "claim_id" not in item:
                raise ValueError("%s:%d: missing claim_id" % (path, line_number))
            claim_id = int(item["claim_id"])
            verdict = str(item.get("verdict") or "").strip().lower()
            if verdict not in ("", *VERDICTS):
                raise ValueError(
                    "%s:%d: verdict must be one of %s" % (path, line_number, ", ".join(VERDICTS))
                )
            effect_verdict = str(item.get("effect_verdict") or "").strip().lower()
            if effect_verdict not in ("", *EFFECT_VERDICTS):
                raise ValueError(
                    "%s:%d: effect_verdict must be one of %s"
                    % (path, line_number, ", ".join(EFFECT_VERDICTS))
                )
            if claim_id in seen:
                raise ValueError("%s:%d: duplicate claim_id %d" % (path, line_number, claim_id))
            seen.add(claim_id)
            verdicts.append(
                {
                    "claim_id": claim_id,
                    "verdict": verdict,
                    "effect_verdict": effect_verdict,
                    "effect_notes": str(item.get("effect_notes") or ""),
                }
            )
    return verdicts


def _score_counts(support: int, refute: int) -> dict[str, Any]:
    evaluated = support + refute
    if evaluated == 0:
        return {
            "support": support,
            "refute": refute,
            "n": 0,
            "precision": None,
            "wilson_low": None,
            "wilson_high": None,
        }
    low, high = stats.wilson_interval(support, evaluated)
    return {
        "support": support,
        "refute": refute,
        "n": evaluated,
        "precision": round(support / evaluated, 4),
        "wilson_low": round(low, 4),
        "wilson_high": round(high, 4),
    }


def score_verdicts(store: Store, verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Join verdicts to claims and compute precision with Wilson intervals.

    ``unclear`` and blank verdicts are reported but excluded from the
    denominator; missing claim ids are surfaced rather than silently dropped.
    """
    rows = {
        int(row["claim_id"]): (
            str(row["role"] or "unknown"),
            str(row["design"] or "unknown"),
            str(row["effect_type"] or ""),
        )
        for row in store.conn.execute("SELECT claim_id, role, design, effect_type FROM claims")
    }
    support = refute = unclear = blank = missing = 0
    effect_sampled = effect_blank = effect_correct = effect_incorrect = effect_unclear = 0
    by_role: dict[str, dict[str, int]] = defaultdict(lambda: {"support": 0, "refute": 0})
    by_design: dict[str, dict[str, int]] = defaultdict(lambda: {"support": 0, "refute": 0})
    by_effect_type: dict[str, dict[str, int]] = defaultdict(lambda: {"support": 0, "refute": 0})
    for item in verdicts:
        claim_id = int(item["claim_id"])
        verdict = str(item.get("verdict") or "")
        if claim_id not in rows:
            missing += 1
            continue
        role, design, effect_type = rows[claim_id]
        if verdict == "":
            blank += 1
        elif verdict == "unclear":
            unclear += 1
        else:
            by_role[role][verdict] += 1
            by_design[design][verdict] += 1
            if verdict == "support":
                support += 1
            else:
                refute += 1
        effect_verdict = str(item.get("effect_verdict") or "")
        if effect_type:
            effect_sampled += 1
            if effect_verdict == "":
                effect_blank += 1
            elif effect_verdict == "unclear":
                effect_unclear += 1
            elif effect_verdict == "correct":
                effect_correct += 1
                by_effect_type[effect_type]["support"] += 1
            elif effect_verdict == "incorrect":
                effect_incorrect += 1
                by_effect_type[effect_type]["refute"] += 1
    return {
        "sampled": len(verdicts),
        "missing": missing,
        "blank": blank,
        "unclear": unclear,
        "overall": _score_counts(support, refute),
        "by_role": {
            name: _score_counts(counts["support"], counts["refute"])
            for name, counts in sorted(by_role.items())
        },
        "by_design": {
            name: _score_counts(counts["support"], counts["refute"])
            for name, counts in sorted(by_design.items())
        },
        "effects": {
            "sampled": effect_sampled,
            "blank": effect_blank,
            "unclear": effect_unclear,
            "overall": _score_counts(effect_correct, effect_incorrect),
            "by_type": {
                name: _score_counts(counts["support"], counts["refute"])
                for name, counts in sorted(by_effect_type.items())
            },
        },
    }


def format_audit_score(result: dict[str, Any]) -> str:
    lines = ["Audit score", "===========", ""]
    lines.append(
        "sampled: %d | evaluated: %d | unclear: %d | blank: %d | missing: %d"
        % (
            result["sampled"],
            result["overall"]["n"],
            result["unclear"],
            result["blank"],
            result["missing"],
        )
    )
    overall = result["overall"]
    if overall["n"] == 0:
        lines.append("precision: n/a (no decisive verdicts)")
    else:
        lines.append(
            "precision: %.3f [95%% Wilson %.3f, %.3f] (%d/%d)"
            % (
                overall["precision"],
                overall["wilson_low"],
                overall["wilson_high"],
                overall["support"],
                overall["n"],
            )
        )
    for label, key in (("role", "by_role"), ("design", "by_design")):
        lines.append("")
        lines.append("by %s:" % label)
        for name, entry in result[key].items():
            if entry["n"] == 0:
                lines.append("  %-12s n/a" % name)
            else:
                lines.append(
                    "  %-12s %.3f [%.3f, %.3f] (%d/%d)"
                    % (
                        name,
                        entry["precision"],
                        entry["wilson_low"],
                        entry["wilson_high"],
                        entry["support"],
                        entry["n"],
                    )
                )
    effects = result.get("effects", {})
    if effects.get("sampled"):
        lines.append("")
        lines.append("effect audit:")
        entry = effects["overall"]
        if entry["n"] == 0:
            lines.append("  precision: n/a (no decisive effect verdicts)")
        else:
            lines.append(
                "  precision: %.3f [95%% Wilson %.3f, %.3f] (%d/%d)"
                % (
                    entry["precision"],
                    entry["wilson_low"],
                    entry["wilson_high"],
                    entry["support"],
                    entry["n"],
                )
            )
        lines.append(
            "  sampled: %d | correct: %d | incorrect: %d | unclear: %d | blank: %d"
            % (
                effects["sampled"],
                entry["support"],
                entry["refute"],
                effects["unclear"],
                effects["blank"],
            )
        )
        for name, entry in effects["by_type"].items():
            lines.append("  %-14s %d/%d" % (name, entry["support"], entry["n"]))
    if result["missing"]:
        lines.append("")
        lines.append("warning: %d verdict(s) reference claims no longer in the database" % result["missing"])
    return "\n".join(lines)
