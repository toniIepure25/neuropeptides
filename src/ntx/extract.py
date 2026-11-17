"""Rule-based claim extraction (v3).

A claim is a sentence-level tuple: (entity, direction, outcome, species, design,
n, p) anchored to an exact character span. Within the sentence, the extractor
also records the clause that carries the evidence, the entity role
(intervention/comparator/mechanism), the abstract section, conservative
quantitative details (effect size, CI and context), and the reasons behind its
confidence. Everything is deterministic and requires no model download.

Provenance invariants:

* ``text[claim.start:claim.end] == claim.sentence``
* ``claim.evidence_start/evidence_end`` is a sub-span of the sentence
* every quantitative quote and context span is an exact sub-span of the sentence
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import Claim, Mention
from .quant import effect_columns, extract_quant

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BOUNDARY = re.compile(
    r";\s+|\(\d{1,2}\)\s+|\s+(?:but|however|whereas|while)\s+",
    re.IGNORECASE,
)

NEGATION = re.compile(
    r"\b(no\s+(?:significant\s+)?(?:effect|difference|change|association)|"
    r"did\s+not|does\s+not|do\s+not|was\s+not|were\s+not|is\s+not|are\s+not|"
    r"failed\s+to|non-?significant|not\s+significant|null\s+(?:result|effect))\b",
    re.IGNORECASE,
)
DECREASE = re.compile(
    r"\b(decreas(?:e|es|ed|ing)|reduc(?:e|es|ed|ing)|reduction|lower(?:ed|s|ing)?|"
    r"attenuat(?:e|es|ed|ing)|suppress(?:ed|es|ing)|inhibit(?:s|ed|ing)\b|"
    r"impair(?:s|ed|ing)\b|down-?regulat\w*|diminish\w*|lessen\w*)\b",
    re.IGNORECASE,
)
INCREASE = re.compile(
    r"\b(increas(?:e|es|ed|ing)|enhanc(?:e|es|ed|ing)|improv(?:e|es|ed|ing)|higher|"
    r"elevat(?:e|es|ed|ing)|promot(?:e|es|ed|ing)|facilitat(?:e|es|ed|ing)|"
    r"up-?regulat\w*|boost\w*|augment\w*|restor(?:e|es|ed|ing))\b",
    re.IGNORECASE,
)
HEDGE = re.compile(
    r"\b(may|might|could|suggest\w*|appear\w*|potential\w*|possibly|likely|seems?)\b", re.IGNORECASE
)
AIM = re.compile(
    r"\bwe\b[^.]{0,80}\b(?:aimed|aims|sought|set out|designed|investigated|examined|evaluated|assessed|studied|characterized|tested)\b"
    r"|\b(?:this|the present|our)\b[^.]{0,60}\b(?:study|review|trial|analysis|meta-analysis|investigation)\b"
    r"[^.]{0,40}\b(?:aimed|aims|sought|set out|was designed|were designed|investigated|examined|evaluated|assessed)\b"
    r"|\bthe (?:aim|purpose|objective) of\b",
    re.IGNORECASE,
)

OUTCOMES: list[tuple[str, re.Pattern]] = [
    (
        "social_cognition",
        re.compile(
            r"social (cognition|behavio|interaction|communicat)|theory of mind|emotion recognition|trust",
            re.I,
        ),
    ),
    ("anxiety", re.compile(r"anxiet|anxious|fear|stress response", re.I)),
    ("depression", re.compile(r"depress|mood|anhedonia", re.I)),
    ("pain", re.compile(r"\bpain\b|nocicept|analgesi|hyperalgesi", re.I)),
    ("feeding", re.compile(r"food intake|feeding|appetite|body weight|weight gain|satiety", re.I)),
    ("memory", re.compile(r"memory|learning|spatial navigation", re.I)),
    ("attention", re.compile(r"attention|vigilance|wakefulness|arousal", re.I)),
    (
        "neuroprotection",
        re.compile(r"neuroprotect|neuronal survival|apoptosis|neurodegenerat|cell death", re.I),
    ),
    (
        "neuroplasticity",
        re.compile(r"neurogenesis|synaptic plasticity|dendritic|long-term potentiation|\bLTP\b", re.I),
    ),
    ("cognition", re.compile(r"cognit|executive function|reaction time|processing speed", re.I)),
]

SPECIES: list[tuple[str, re.Pattern]] = [
    (
        "human",
        re.compile(
            r"\b(participants?|patients?|subjects?|volunteers?|children|adults|healthy controls?|women|men)\b",
            re.I,
        ),
    ),
    ("rat", re.compile(r"\brats?\b", re.I)),
    ("mouse", re.compile(r"\bmice\b|\bmouse\b", re.I)),
    ("macaque", re.compile(r"\bmacaques?\b|\bmonkeys?\b", re.I)),
    ("zebrafish", re.compile(r"\bzebrafish\b", re.I)),
    ("dog", re.compile(r"\bdogs?\b", re.I)),
    ("in_vitro", re.compile(r"\bcells?\b|in vitro|cultured|hippocampal slices", re.I)),
]

DESIGNS: list[tuple[str, re.Pattern]] = [
    ("meta_analysis", re.compile(r"meta-analys|meta analys", re.I)),
    ("systematic_review", re.compile(r"systematic review", re.I)),
    ("rct", re.compile(r"randomi[sz]ed|randomly assigned|placebo-controlled|\bRCT\b", re.I)),
    ("trial", re.compile(r"\bclinical trial\b|\btrial\b", re.I)),
    ("cohort", re.compile(r"\bcohort\b|longitudinal", re.I)),
    ("case_control", re.compile(r"case-control|case control", re.I)),
    ("cross_sectional", re.compile(r"cross-sectional|cross sectional", re.I)),
    ("case_report", re.compile(r"case report", re.I)),
    ("animal", re.compile(r"\bin (rats|mice)\b|\banimal model\b|\brodents?\b", re.I)),
    ("in_vitro", re.compile(r"\bin vitro\b|\bcultured cells?\b|\bcell line\b", re.I)),
    ("review", re.compile(r"\breview\b", re.I)),
]

N_RE = re.compile(r"\bn\s*=\s*(\d{1,6})", re.I)
N_WORD_RE = re.compile(
    r"\b(\d{1,6})\s+(participants?|patients?|subjects?|volunteers?|mice|rats|children|adults|women|men)\b",
    re.I,
)
P_RE = re.compile(r"\bp\s*[<>=]\s*(0?\.\d+)", re.I)

METHOD = re.compile(
    r"\b(?:was|were)\s+(?:calculated|derived|defined|modell?ed|computed)\s+as\b", re.IGNORECASE
)

BACKGROUND = re.compile(
    r"\b(?:has|have)\s+been\s+shown\b|\bprevious(?:ly)?\s+(?:studies|work|research)\b|"
    r"\bevidence\s+suggests\b|\bit\s+is\s+(?:well\s+)?known\b|\bstudies\s+have\s+shown\b",
    re.IGNORECASE,
)
SECTION_LABEL = re.compile(
    r"^\s*(?:objective|background|importance|introduction|methods?|design|setting|participants|"
    r"interventions?|main outcomes?|measurements?)\b",
    re.IGNORECASE,
)
_CONTRAST = re.compile(r"\b(but|however|yet|whereas|while)\b", re.IGNORECASE)

_SECTION_RE = re.compile(
    r"\b(background|introduction|objective|aim|purpose|methods?|design|results?|findings|"
    r"conclusions?|interpretation)\s*:",
    re.IGNORECASE,
)
_SECTION_NAMES = {
    "background": "background",
    "introduction": "introduction",
    "objective": "objective",
    "aim": "objective",
    "purpose": "objective",
    "method": "methods",
    "methods": "methods",
    "design": "methods",
    "result": "results",
    "results": "results",
    "finding": "results",
    "findings": "results",
    "conclusion": "conclusions",
    "conclusions": "conclusions",
    "interpretation": "conclusions",
}
_EXCLUDED_SECTIONS = {"background", "introduction", "objective", "methods"}

_COMPARATOR_BEFORE = re.compile(r"\b(?:compared\s+(?:with|to)|versus|vs\.?|than|relative\s+to)\s*$", re.I)
_ALONE_AFTER = re.compile(r"^\s*(?:\w+\s+){0,3}alone\b", re.I)
_MECHANISM_BEFORE = re.compile(r"\b(?:via|through)\s*$", re.I)
_DEFINITE_NP = re.compile(
    r"\b(?:the|this|these|that)\s+(?:peptide|neuropeptide|factor|compound|drug|substance|molecule|protein)s?\b",
    re.I,
)


@dataclass(frozen=True)
class FeatureFlags:
    """Deterministic feature switches used by ablation runs."""

    clause_direction: bool = True
    comparator_role: bool = True
    section_filter: bool = True
    resolve_coref: bool = False
    use_annotations: bool = True
    quantitative: bool = True


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) pairs whose slice is exactly the sentence text."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENT_SPLIT.finditer(text):
        end = match.start()
        if end > cursor:
            spans.append((cursor, end))
        cursor = match.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    trimmed: list[tuple[int, int]] = []
    for start, end in spans:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end - start > 15:
            trimmed.append((start, end))
    return trimmed


def split_clauses(sentence: str, min_length: int = 8) -> list[tuple[int, int]]:
    """Split a sentence into clause spans at contrast, enumeration and semicolons."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _CLAUSE_BOUNDARY.finditer(sentence):
        if match.start() > cursor:
            spans.append((cursor, match.start()))
        cursor = match.end()
    if cursor < len(sentence):
        spans.append((cursor, len(sentence)))
    trimmed: list[tuple[int, int]] = []
    for start, end in spans:
        while start < end and sentence[start].isspace():
            start += 1
        while end > start and sentence[end - 1].isspace():
            end -= 1
        if end - start >= min_length:
            trimmed.append((start, end))
    if not trimmed:
        return [(0, len(sentence))] if sentence.strip() else []
    return trimmed


def _clause_at(clauses: list[tuple[int, int]], position: int) -> tuple[int, int]:
    for start, end in clauses:
        if start <= position < end:
            return start, end
    for start, end in clauses:
        if start >= position:
            return start, end
    return clauses[-1]


def _first(patterns: list[tuple[str, re.Pattern]], text: str, default: str) -> str:
    for label, pattern in patterns:
        if pattern.search(text):
            return label
    return default


def _direction(sentence: str, rel_start: int = 0, use_clauses: bool = True) -> str:
    whole_neg = bool(NEGATION.search(sentence))
    whole_dec = bool(DECREASE.search(sentence))
    whole_inc = bool(INCREASE.search(sentence))
    if whole_neg and (whole_dec or whole_inc) and _CONTRAST.search(sentence):
        # "no effect at 24 h but reduced at 3 h" is a mixed result.
        return "mixed"

    if use_clauses:
        span = _clause_at(split_clauses(sentence), rel_start)
        segment = sentence[span[0] : span[1]]
    else:
        segment = sentence[rel_start:]

    neg = bool(NEGATION.search(segment))
    dec = bool(DECREASE.search(segment))
    inc = bool(INCREASE.search(segment))
    if neg:
        return "none"
    if dec and inc:
        return "mixed"
    if inc:
        return "increase"
    if dec:
        return "decrease"
    if use_clauses and rel_start > 0:
        # The clause may start after the entity; fall back to the tail so a
        # direction stated later in the sentence is not lost.
        return _direction(sentence[rel_start:], 0, use_clauses=True)
    return "unclear"


def _sample_size(sentence: str) -> int | None:
    values: list[int] = []
    for match in N_RE.finditer(sentence):
        values.append(int(match.group(1)))
    for match in N_WORD_RE.finditer(sentence):
        values.append(int(match.group(1)))
    if not values:
        return None
    return max(values)


def _p_value(sentence: str) -> float | None:
    match = P_RE.search(sentence)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _mentions_in(mentions: list[Mention], start: int, end: int) -> list[Mention]:
    return [
        mention for mention in mentions if mention.accepted and mention.start >= start and mention.end <= end
    ]


def _role(sentence: str, start: int, end: int) -> str:
    before = sentence[max(0, start - 40) : start]
    after = sentence[end : end + 30]
    if _COMPARATOR_BEFORE.search(before) or _ALONE_AFTER.match(after):
        return "comparator"
    if _MECHANISM_BEFORE.search(before):
        return "mechanism"
    return "intervention"


def sections(text: str, offset: int = 0) -> list[tuple[int, str]]:
    """Return ``(absolute_start, canonical_name)`` for structured abstract labels."""
    found: list[tuple[int, str]] = []
    for match in _SECTION_RE.finditer(text):
        name = _SECTION_NAMES.get(match.group(1).lower())
        if name:
            found.append((match.start() + offset, name))
    return found


def _section_at(labels: list[tuple[int, str]], position: int) -> str:
    current = "abstract"
    for start, name in labels:
        if start <= position:
            current = name
        else:
            break
    return current


def _evidence_span(sentence: str, rel_start: int, rel_end: int) -> tuple[int, int]:
    clauses = split_clauses(sentence)
    span = _clause_at(clauses, rel_start)
    if span[1] - span[0] < 15:
        return 0, len(sentence)
    return span


def _quant_fields(sentence: str, start: int, enabled: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    quant = extract_quant(sentence, start) if enabled else {}
    return quant, effect_columns(quant)


def extract_claims(
    text: str,
    mentions: list[Mention],
    offset: int = 0,
    flags: FeatureFlags | None = None,
    section_labels: list[tuple[int, str]] | tuple[tuple[int, str], ...] | None = None,
) -> list[Claim]:
    """Extract claims from ``text[offset:]``.

    ``offset`` lets the pipeline skip the title, which otherwise produces
    question-shaped "claims" such as "Does oxytocin reduce symptoms?".

    ``section_labels`` supplies authoritative section offsets, as used by the
    full-text layer. Without it, headings are detected from the abstract text.
    """
    flags = flags or FeatureFlags()
    claims: list[Claim] = []
    seen: set[tuple[str, int, int]] = set()
    doc_design = _first(DESIGNS, text, "unknown")
    doc_species = _first(SPECIES, text, "unspecified")
    if section_labels is not None:
        labels = list(section_labels)
    else:
        labels = sections(text) if flags.section_filter else []
    history: list[tuple[str, int, int]] = []

    for rel_start, rel_end in split_sentences(text[offset:]):
        start, end = rel_start + offset, rel_end + offset
        sentence = text[start:end]
        if sentence.rstrip().endswith("?"):
            continue
        if AIM.search(sentence):
            continue
        if METHOD.search(sentence):
            continue
        if BACKGROUND.search(sentence) and not re.search(r"\d", sentence):
            continue
        if SECTION_LABEL.search(sentence):
            continue
        section = _section_at(labels, start) if labels else "abstract"
        if flags.section_filter and section in _EXCLUDED_SECTIONS and not re.search(r"\d", sentence):
            continue

        local = _mentions_in(mentions, start, end)
        outcome = _first(OUTCOMES, sentence, "other")
        design = _first(DESIGNS, sentence, doc_design)
        species = _first(SPECIES, sentence, doc_species)
        n = _sample_size(sentence)
        p = _p_value(sentence)
        hedged = bool(HEDGE.search(sentence))

        if not local and flags.resolve_coref:
            match = _DEFINITE_NP.search(sentence)
            candidates = {entity for entity, _s, _e in history[-6:]}
            if match and len(candidates) == 1:
                entity_id = next(iter(candidates))
                direction = _direction(sentence, match.start(), flags.clause_direction)
                if direction != "unclear":
                    evidence_start, evidence_end = _evidence_span(sentence, match.start(), match.end())
                    quant, effect = _quant_fields(sentence, start, flags.quantitative)
                    coref_reasons = ["coref_antecedent"]
                    if design != "unknown":
                        coref_reasons.append("design_detected")
                    claims.append(
                        Claim(
                            doc_id="",
                            entity_id=entity_id,
                            direction=direction,
                            outcome=outcome,
                            species=species,
                            design=design,
                            sample_size=n,
                            p_value=p,
                            effect_type=str(effect["effect_type"]),
                            effect_size=effect["effect_size"],
                            effect_ci_low=effect["effect_ci_low"],
                            effect_ci_high=effect["effect_ci_high"],
                            quant=quant,
                            hedged=hedged,
                            negated=direction == "none",
                            sentence=sentence,
                            start=start,
                            end=end,
                            extractor="rules-v3",
                            confidence=0.45,
                            role=_role(sentence, match.start(), match.end())
                            if flags.comparator_role
                            else "intervention",
                            evidence_start=evidence_start + start,
                            evidence_end=evidence_end + start,
                            section=section,
                            resolver="antecedent",
                            reasons=coref_reasons,
                        )
                    )
                    history.extend((mention.entity_id, mention.start, mention.end) for mention in local)
                    continue

        for mention in local:
            key = (mention.entity_id, start, end)
            if key in seen:
                continue
            rel_mention_start = mention.start - start
            direction = _direction(sentence, rel_mention_start, use_clauses=flags.clause_direction)
            if direction == "unclear":
                continue
            seen.add(key)
            role = (
                _role(sentence, rel_mention_start, mention.end - start)
                if flags.comparator_role
                else "intervention"
            )
            evidence_start, evidence_end = _evidence_span(sentence, rel_mention_start, mention.end - start)
            quant, effect = _quant_fields(sentence, start, flags.quantitative)

            reasons: list[str] = ["explicit_entity"]
            confidence = 0.55
            if design != "unknown":
                reasons.append("design_detected")
                confidence += 0.10
            if n:
                reasons.append("sample_size")
                confidence += 0.10
            if p is not None:
                reasons.append("p_value")
                confidence += 0.05
            if hedged:
                reasons.append("hedged")
                confidence += 0.05
            if role != "intervention":
                reasons.append("role_" + role)
                confidence -= 0.10
            if _CONTRAST.search(sentence):
                reasons.append("clause_contrast")
            if section in ("results", "conclusions"):
                reasons.append("results_section")
                confidence += 0.05

            claims.append(
                Claim(
                    doc_id="",
                    entity_id=mention.entity_id,
                    direction=direction,
                    outcome=outcome,
                    species=species,
                    design=design,
                    sample_size=n,
                    p_value=p,
                    effect_type=str(effect["effect_type"]),
                    effect_size=effect["effect_size"],
                    effect_ci_low=effect["effect_ci_low"],
                    effect_ci_high=effect["effect_ci_high"],
                    quant=quant,
                    hedged=hedged,
                    negated=direction == "none",
                    sentence=sentence,
                    start=start,
                    end=end,
                    extractor="rules-v3",
                    confidence=round(max(0.05, min(confidence, 0.95)), 2),
                    role=role,
                    evidence_start=evidence_start + start,
                    evidence_end=evidence_end + start,
                    section=section,
                    resolver="direct",
                    reasons=reasons,
                )
            )
        history.extend((mention.entity_id, mention.start, mention.end) for mention in local)
        history = history[-6:]
    return claims
