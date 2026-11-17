"""Hypothesis generation for peptide discovery.

This module does not discover molecules. It mines the extracted evidence for
*testable gaps* and ranks them, so wet-lab time goes to questions the literature
cannot already answer: unresolved contradictions, claims that exist only in
animals or in vitro, and mechanisms that were never measured.

Every hypothesis keeps the doc_ids it was derived from; nothing is generated
without a traceable evidence base.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .store import Store

# Outcomes worth a dedicated experiment, with the cheapest assay that can
# falsify the hypothesis. Kept deliberately small and auditable.
OUTCOME_ASSAYS: dict[str, str] = {
    "cognition": "Rodent novel-object recognition + Barnes maze; human CogTrack battery",
    "memory": "Fear conditioning / radial arm maze; human verbal learning",
    "social_cognition": "Three-chamber sociability + human emotion-recognition task",
    "anxiety": "Elevated plus maze / light-dark box; human threat-of-shock task",
    "depression": "Forced swim / sucrose preference; human mood induction",
    "feeding": "24 h food intake + body weight; human appetite VAS",
    "pain": "Von Frey / hot plate; human cold-pressor",
    "attention": "5-choice serial reaction time; human PVT",
    "neuroplasticity": "Hippocampal LTP slices + dendritic spine density",
    "neuroprotection": "Primary neuron excitotoxicity + caspase-3",
}

BEHAVIORAL = {
    "cognition",
    "memory",
    "social_cognition",
    "anxiety",
    "depression",
    "feeding",
    "pain",
    "attention",
}
MECHANISTIC = {"neuroplasticity", "neuroprotection"}
HUMAN_STRONG = {"meta_analysis", "systematic_review", "rct"}
ANIMAL_WEAK = {"animal", "in_vitro", "review", "unknown"}

CLASS_WEIGHT = {
    "contradiction": 3.0,
    "weak_evidence": 2.2,
    "mechanism_gap": 1.6,
    "untested": 1.0,
}


@dataclass
class Hypothesis:
    hypothesis_id: str
    kind: str
    entity_id: str
    outcome: str
    statement: str
    rationale: str
    doc_ids: list[str]
    score: float
    assay: str
    design: str
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "kind": self.kind,
            "entity_id": self.entity_id,
            "outcome": self.outcome,
            "statement": self.statement,
            "rationale": self.rationale,
            "doc_ids": self.doc_ids,
            "score": round(self.score, 2),
            "assay": self.assay,
            "design": self.design,
            "risks": self.risks,
        }


def _hid(kind: str, entity_id: str, outcome: str) -> str:
    return "H-" + hashlib.sha1(("%s|%s|%s" % (kind, entity_id, outcome)).encode()).hexdigest()[:8]


def _design_for(kind: str, designs: Iterable[str]) -> str:
    if kind == "contradiction":
        return (
            "Pre-registered, randomized, blinded head-to-head study in the model where the "
            "contradiction arose; dose-response with a pre-specified primary outcome."
        )
    if kind == "weak_evidence":
        return (
            "Translational dose-response: rodent PK/PD + behaviour first, then a randomized "
            "placebo-controlled pilot in humans with CSF or plasma exposure confirmation."
        )
    if kind == "mechanism_gap":
        return (
            "Causal mechanism test: receptor antagonist + knockout/knockdown, slice "
            "electrophysiology, and pathway readout at behaviourally active doses."
        )
    return "Exploratory dose-finding study with a registered primary outcome and a replication arm."


def _risks(entity_id: str, outcome: str) -> list[str]:
    risks = [
        "BBB/CSF exposure unproven for a peptide; measure it before efficacy claims",
        "rapid proteolysis (DPP-IV/NEP) may make the observed effect short-lived",
        "outcome confounded by motor, arousal, or motivational effects",
    ]
    if outcome in BEHAVIORAL:
        risks.append("behavioural battery needs counterbalancing and blinded scoring")
    return risks


def generate(store: Store, top: int = 20, include_untested: bool = True) -> list[Hypothesis]:
    rows = [
        dict(r)
        for r in store.conn.execute(
            "SELECT c.entity_id, c.outcome, c.direction, c.design, c.doc_id FROM claims c"
        )
    ]
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed_outcomes: dict[str, set] = defaultdict(set)
    for row in rows:
        by_pair[(row["entity_id"], row["outcome"])].append(row)
        by_entity[row["entity_id"]].append(row)
        observed_outcomes[row["entity_id"]].add(row["outcome"])

    hypotheses: list[Hypothesis] = []

    for (entity_id, outcome), items in by_pair.items():
        docs = sorted({i["doc_id"] for i in items})
        directions = Counter(i["direction"] for i in items)
        designs = {i["design"] for i in items}
        has_human_strong = bool(designs & HUMAN_STRONG)

        if directions["increase"] > 0 and directions["decrease"] > 0:
            score = CLASS_WEIGHT["contradiction"] + min(len(docs), 5) * 0.2
            risks = _risks(entity_id, outcome)
            if outcome == "other":
                # A contradiction on an unspecified endpoint is often two
                # different measurements being compared, not a real disagreement.
                score -= 0.5
                risks.insert(0, "outcome is 'other'; split by measured variable before testing")
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=_hid("contradiction", entity_id, outcome),
                    kind="contradiction",
                    entity_id=entity_id,
                    outcome=outcome,
                    statement="Resolve the opposing effects of %s on %s" % (entity_id, outcome),
                    rationale="%d claims disagree in direction across %d documents (increase=%d, decrease=%d)."
                    % (len(items), len(docs), directions["increase"], directions["decrease"]),
                    doc_ids=docs,
                    score=score,
                    assay=OUTCOME_ASSAYS.get(outcome, "outcome-specific assay"),
                    design=_design_for("contradiction", designs),
                    risks=risks,
                )
            )
            continue

        if not has_human_strong and designs & ANIMAL_WEAK:
            observational = bool(designs & {"cohort", "case_control", "cross_sectional"})
            if observational:
                statement = "Test whether the association between %s and %s is causal" % (entity_id, outcome)
                rationale = (
                    "Evidence is non-experimental (designs: %s); no randomized or pooled estimate."
                    % ", ".join(sorted(designs))
                )
                base = CLASS_WEIGHT["weak_evidence"] + 0.2
            else:
                statement = "Test whether the %s effect of %s translates to humans" % (outcome, entity_id)
                rationale = "Evidence exists only in %s; no RCT or meta-analysis." % ", ".join(
                    sorted(designs)
                )
                base = CLASS_WEIGHT["weak_evidence"]
            score = base + min(len(docs), 5) * 0.2
            risks = _risks(entity_id, outcome)
            if outcome == "other":
                score -= 0.4
                risks.append("outcome is 'other'; define a measurable endpoint first")
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=_hid("weak_evidence", entity_id, outcome),
                    kind="weak_evidence",
                    entity_id=entity_id,
                    outcome=outcome,
                    statement=statement,
                    rationale=rationale,
                    doc_ids=docs,
                    score=score,
                    assay=OUTCOME_ASSAYS.get(outcome, "outcome-specific assay"),
                    design=_design_for("weak_evidence", designs),
                    risks=risks,
                )
            )

    # Mechanism gap: behavioural effects without any mechanistic readout.
    for entity_id, items in by_entity.items():
        outcomes = observed_outcomes[entity_id]
        if outcomes & BEHAVIORAL and not outcomes & MECHANISTIC:
            docs = sorted({i["doc_id"] for i in items})
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=_hid("mechanism_gap", entity_id, "neuroplasticity"),
                    kind="mechanism_gap",
                    entity_id=entity_id,
                    outcome="neuroplasticity",
                    statement="Identify the synaptic mechanism behind the behavioural effects of %s"
                    % entity_id,
                    rationale="Behavioural claims (%s) exist, but no plasticity or neuroprotection readout."
                    % ", ".join(sorted(outcomes & BEHAVIORAL)),
                    doc_ids=docs,
                    score=CLASS_WEIGHT["mechanism_gap"] + min(len(docs), 5) * 0.2,
                    assay=OUTCOME_ASSAYS["neuroplasticity"],
                    design=_design_for("mechanism_gap", set()),
                    risks=_risks(entity_id, "neuroplasticity"),
                )
            )

    # Untested combinations: entity has evidence, target outcome never appears.
    if include_untested:
        for entity_id, items in by_entity.items():
            docs = sorted({i["doc_id"] for i in items})
            for outcome in ("cognition", "anxiety", "social_cognition", "neuroprotection"):
                if outcome in observed_outcomes[entity_id]:
                    continue
                hypotheses.append(
                    Hypothesis(
                        hypothesis_id=_hid("untested", entity_id, outcome),
                        kind="untested",
                        entity_id=entity_id,
                        outcome=outcome,
                        statement="Test %s for a %s effect (never reported)" % (entity_id, outcome),
                        rationale="No extracted claim links %s to %s in %d documents; low prior, cheap screen."
                        % (entity_id, outcome, len(docs)),
                        doc_ids=docs,
                        score=CLASS_WEIGHT["untested"] + min(len(docs), 5) * 0.1,
                        assay=OUTCOME_ASSAYS.get(outcome, "outcome-specific assay"),
                        design=_design_for("untested", set()),
                        risks=_risks(entity_id, outcome),
                    )
                )

    hypotheses.sort(key=lambda h: (h.score, h.kind != "untested"), reverse=True)
    return hypotheses[:top]


def format_report(hypotheses: list[Hypothesis]) -> str:
    lines = ["Ranked discovery hypotheses", "===========================", ""]
    if not hypotheses:
        return "\n".join(lines + ["No claims in the database; ingest or run the demo first."])
    for i, h in enumerate(hypotheses, 1):
        lines.append("%2d. [%.2f] %s - %s" % (i, h.score, h.kind, h.statement))
        lines.append("    id: %s | evidence: %d doc(s)" % (h.hypothesis_id, len(h.doc_ids)))
        lines.append("    why: %s" % h.rationale)
        lines.append("    assay: %s" % h.assay)
        lines.append("    design: %s" % h.design)
        lines.append("    risks: %s" % "; ".join(h.risks[:2]))
        lines.append("")
    return "\n".join(lines)
