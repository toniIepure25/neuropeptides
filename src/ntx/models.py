"""Core data models with explicit provenance fields.

Every mention and claim carries the document identifier and an exact character
span into the source text. The store enforces that the span is a substring of
the stored document text, so a claim can always be traced back to a quote.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Document:
    doc_id: str
    source: str = "europepmc"
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    title: str = ""
    abstract: str = ""
    journal: str = ""
    year: int | None = None
    pubtypes: list[str] = field(default_factory=list)
    retraction_status: str = "none"  # none | concern | retracted | unknown
    retraction_note: str = ""
    license: str = ""
    raw_path: str = ""
    content_hash: str = ""
    fetched_at: str = ""

    def text(self) -> str:
        return (self.title + "\n\n" + self.abstract).strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Mention:
    doc_id: str
    entity_id: str
    surface: str
    start: int
    end: int
    section: str = "abstract"
    method: str = "gazetteer"  # gazetteer | annotation | acronym
    confidence: float = 0.9
    accepted: bool = True
    reason: str = ""


@dataclass
class Claim:
    doc_id: str
    entity_id: str
    direction: str  # increase | decrease | none | mixed | unclear
    outcome: str = "other"
    species: str = "unspecified"
    design: str = "unknown"
    sample_size: int | None = None
    p_value: float | None = None
    effect_type: str = ""
    effect_size: float | None = None
    effect_ci_low: float | None = None
    effect_ci_high: float | None = None
    quant: dict[str, Any] = field(default_factory=dict)
    hedged: bool = False
    negated: bool = False
    sentence: str = ""
    start: int = 0
    end: int = 0
    extractor: str = "rules-v3"
    confidence: float = 0.5
    role: str = "intervention"  # intervention | comparator | mechanism | unknown
    evidence_start: int = 0
    evidence_end: int = 0
    section: str = "abstract"
    resolver: str = "direct"  # direct | antecedent
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Appraisal:
    doc_id: str
    entity_id: str
    start: int
    end: int
    design: str = "unknown"
    retraction_status: str = "none"
    bias_flags: list[str] = field(default_factory=list)
    funding_flag: bool = False
    coi_flag: bool = False
    rubric_points: float = 0.0
    band: str = "low"  # low | moderate | high
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    candidate_id: str
    name: str
    sequence: str
    length: int
    target_entity_id: str = ""
    hypothesis_id: str = ""
    modifications: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    provenance: str = "user-supplied"
    notes: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Experiment:
    experiment_id: str
    hypothesis_id: str
    hypothesis_kind: str
    entity_id: str
    outcome: str
    hypothesis_snapshot: dict[str, Any]
    candidate_id: str = ""
    design: str = ""
    assay: str = ""
    primary_outcome: str = ""
    n_planned: int = 0
    preregistration: str = ""
    power: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Precursor:
    precursor_id: str
    name: str
    sequence: str
    length: int
    source: str = "user-supplied"
    uniprot: str = ""
    notes: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateLineage:
    lineage_id: str
    candidate_id: str
    operation: str
    source_kind: str
    source_id: str = ""
    parent_id: str = ""
    start: int | None = None
    end: int | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Prediction:
    prediction_id: str
    experiment_id: str
    direction: str
    probability: float
    rationale: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Outcome:
    outcome_id: str
    experiment_id: str
    observed_direction: str
    effect_size: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    n: int | None = None
    qc: str = "pass"
    note: str = ""
    source_id: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
