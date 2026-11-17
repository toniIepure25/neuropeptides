"""Evidence appraisal: design, integrity flags, and a transparent rubric.

The rubric is an intentionally simple, auditable triage signal. It is NOT a
probability that a claim is true, and the report says so.
"""

from __future__ import annotations

import re
from typing import Any

from .models import Appraisal, Claim, Document

DESIGN_POINTS = {
    "meta_analysis": 3.0,
    "systematic_review": 2.5,
    "rct": 3.0,
    "trial": 2.0,
    "cohort": 2.0,
    "case_control": 1.5,
    "cross_sectional": 1.0,
    "case_report": 0.5,
    "animal": 1.0,
    "in_vitro": 0.5,
    "review": 0.5,
    "unknown": 0.5,
}

PUBTYPE_DESIGN = [
    ("meta-analysis", "meta_analysis"),
    ("systematic review", "systematic review"),
    ("randomized controlled trial", "rct"),
    ("clinical trial", "trial"),
    ("cohort", "cohort"),
    ("case-control", "case_control"),
    ("cross-sectional", "cross_sectional"),
    ("case reports", "case_report"),
    ("review", "review"),
]

PREREG = re.compile(r"pre-?regist|clinicaltrials\.gov|registered (?:trial|protocol)|osf\.io", re.I)
INDUSTRY = re.compile(
    r"funded by (?:the )?(?:[A-Z][\w&.\-]+ ){0,4}(?:pharmaceutical|pharma|industry|inc\.|ltd\.?|corporation)",
    re.I,
)
COI = re.compile(r"conflict of interest|competing interests|declaration of interest", re.I)


def design_from_pubtypes(pubtypes: list[str]) -> str | None:
    joined = " | ".join(t.lower() for t in pubtypes)
    for needle, design in PUBTYPE_DESIGN:
        if needle in joined:
            return design
    return None


def retraction_from_pubtypes(pubtypes: list[str]) -> str | None:
    joined = " | ".join(t.lower() for t in pubtypes)
    if "retraction of publication" in joined:
        return "retraction_notice"
    if "retracted publication" in joined:
        return "retracted"
    if "expression of concern" in joined:
        return "concern"
    return None


def merge_crossref_status(current: str, payload: dict[str, Any] | None) -> str:
    """Combine PubMed pubtypes with Crossref update relations (retraction wins)."""
    if not payload:
        return current
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    updates = message.get("update-to") or []
    for update in updates:
        if (update.get("type") or "").lower() == "retraction":
            return "retracted"
        if (update.get("type") or "").lower() == "expression-of-concern":
            return "concern"
    return current


def classify_design(doc: Document) -> str:
    from_meta = design_from_pubtypes(doc.pubtypes)
    if from_meta:
        return from_meta
    text = doc.text().lower()
    if "meta-analys" in text or "meta analys" in text:
        return "meta_analysis"
    if "systematic review" in text:
        return "systematic_review"
    if "randomized" in text or "randomised" in text or "placebo-controlled" in text:
        return "rct"
    if "cohort" in text:
        return "cohort"
    if "case-control" in text or "case control" in text:
        return "case_control"
    if "cross-sectional" in text or "cross sectional" in text:
        return "cross_sectional"
    if re.search(r"\b(rats|mice|mouse|rodents)\b", text):
        return "animal"
    if re.search(r"\b(cells?|in vitro|cell line)\b", text):
        return "in_vitro"
    return "unknown"


def bias_flags(doc: Document, design: str) -> list[str]:
    text = doc.text()
    low = text.lower()
    flags: list[str] = []
    if design in {"rct", "trial"}:
        if "blind" not in low and "mask" not in low:
            flags.append("no_blinding_reported")
        if "random" not in low:
            flags.append("randomization_unclear")
    if design in {"cohort", "case_control", "cross_sectional"}:
        flags.append("observational_no_causal_claim")
    if doc.retraction_status in {"retracted", "concern"}:
        flags.append("integrity_notice:" + doc.retraction_status)
    if "preprint" in low or "not peer reviewed" in low:
        flags.append("preprint")
    if not doc.doi and not doc.pmid:
        flags.append("no_stable_identifier")
    return flags


def rubric(doc: Document, claim: Claim, design: str) -> tuple[float, str, list[str]]:
    reasons: list[str] = []
    points = DESIGN_POINTS.get(design, 0.5)
    reasons.append("design=%s (+%.1f)" % (design, points))

    if claim.sample_size:
        if claim.sample_size >= 100:
            points += 1.0
            reasons.append("n>=100 (+1.0)")
        elif claim.sample_size >= 30:
            points += 0.5
            reasons.append("n>=30 (+0.5)")
    if PREREG.search(doc.text()):
        points += 0.5
        reasons.append("pre-registration mentioned (+0.5)")
    if claim.p_value is not None and claim.p_value < 0.05:
        points += 0.5
        reasons.append("reported p<0.05 (+0.5)")
    if INDUSTRY.search(doc.text()):
        points -= 1.0
        reasons.append("industry funding (-1.0)")
    if claim.design in {"rct", "trial"} and "blind" not in doc.text().lower():
        points -= 0.5
        reasons.append("no blinding reported (-0.5)")
    if doc.retraction_status == "retracted":
        points -= 3.0
        reasons.append("RETRACTED (-3.0)")
    elif doc.retraction_status == "concern":
        points -= 1.5
        reasons.append("expression of concern (-1.5)")

    points = max(0.0, round(points, 2))
    if points >= 3.5:
        band = "high"
    elif points >= 2.0:
        band = "moderate"
    else:
        band = "low"
    return points, band, reasons


def appraise_claims(doc: Document, claims: list[Claim]) -> list[Appraisal]:
    design = classify_design(doc)
    flags = bias_flags(doc, design)
    out: list[Appraisal] = []
    for claim in claims:
        points, band, reasons = rubric(doc, claim, design)
        out.append(
            Appraisal(
                doc_id=doc.doc_id,
                entity_id=claim.entity_id,
                start=claim.start,
                end=claim.end,
                design=design,
                retraction_status=doc.retraction_status,
                bias_flags=list(flags),
                funding_flag=bool(INDUSTRY.search(doc.text())),
                coi_flag=bool(COI.search(doc.text())),
                rubric_points=points,
                band=band,
                reasons=reasons,
            )
        )
    return out


def consistency(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Direction tally per (entity, outcome) with a contradiction flag."""
    tally: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["entity_id"], row["outcome"])
        bucket = tally.setdefault(key, {"increase": 0, "decrease": 0, "none": 0, "mixed": 0, "docs": set()})
        direction = row["direction"]
        if direction in bucket:
            bucket[direction] += 1
        bucket["docs"].add(row["doc_id"])
    for bucket in tally.values():
        bucket["contradiction"] = bucket["increase"] > 0 and bucket["decrease"] > 0
        bucket["doc_count"] = len(bucket["docs"])
        bucket.pop("docs", None)
    return tally
