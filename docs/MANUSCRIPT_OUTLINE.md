# Manuscript outline (target: methods/tools paper)

Working title: *Provenance-first surveillance of neuropeptide, neurotrophic and
neurotropic claims in the biomedical literature.*

Every result below names its source artefact and its evidence status. Items marked
`TBD` must not be filled with draft numbers.

## Abstract (150–200 words)

- Problem: claims about peptides and neurotropic substances are fragmented across
  designs and contradicted across papers; reviews cannot keep up.
- Contribution: an auditable extraction/appraisal pipeline with a span-level
  provenance invariant, a reproducible run manifest, and a stratified benchmark.
- Results: TBD after gold v2 adjudication; pilot numbers reported separately.
- Availability: MIT code, CC BY 4.0 docs, archived release with DOI.

## 1. Introduction

1.1 Why neuropeptides/neurotrophics/neurotropics are a surveillance problem
(overlapping nomenclature, supplement market, heterogeneous evidence).
1.2 Failure modes of existing databases: entity conflation, no claim-level
provenance, no uncertainty reporting.
1.3 Contributions (bulleted, each mapping to a section).

## 2. Related work

- Biomedical NER/normalization (PubTator, Europe PMC annotations) and why
  annotations are candidates, not ground truth.
- Claim/evidence extraction and appraisal (semantic predications, evidence
  grading).
- Reproducibility infrastructure for NLP pipelines.

## 3. System

3.1 Architecture (Figure 1: ingest → detect → extract → appraise → store → report).
3.2 Entity model: NP/NTF/NTS, acronym abstention, generic-token rejection.
3.3 Claim model and the span invariant; report refusal semantics.
3.4 Appraisal rubric and its explicit non-probabilistic status.
3.5 Reproducibility: frozen time, content-addressed manifests, schema migrations.
3.6 Discovery mode (hypothesis triage) and its limits.

## 4. Benchmark

4.1 Pilot gold (6 documents, single annotator) — describe as pilot only.
4.2 Gold v2 sampling frame: 5 compounds × 3 strata, 119 documents, frozen queries
and hashes (`gold/v2/sampling_frame.json`).
4.3 Annotation protocol, double annotation of 30 documents, adjudication,
κ per field (`gold/PROTOCOL_V2.md`).
4.4 Draft pre-annotations: disclosed as model output, not gold.

## 5. Evaluation

5.1 Metrics and CIs (document-level bootstrap).
5.2 Main results table: mention span P/R/F1, ID accuracy, claim strict/relaxed
F1, per stratum. Status: **TBD until human gold exists**.
5.3 Ablations (six pre-registered in `docs/METHOD.md` §6). Status: TBD.
5.4 Error analysis with taxonomy and counts from the adjudicated set. Status: TBD.
5.5 Pilot results (n = 6): strict claim F1 0.552, direction-relaxed 0.762 —
reported with the explicit caveat that CIs are wide and mentions are unscored.

## 6. Discussion

- What the provenance invariant buys and what it does not (span match ≠ claim
  truth).
- Default abstract-only extraction; optional PMC JATS section metadata is available
  but does not cover publisher PDFs. Coreference remains future work.
- Rubric calibration requires external outcomes; no probability claims until then.
- Governance: no abstract redistribution, licence tracking, audit trail.

## 7. Limitations (must mirror `docs/METHOD.md` §5 and README)

- 119-document frame, not a systematic review sample.
- Single-annotator pilot; v2 double annotation pending.
- English-only, Europe PMC-indexed literature.
- Lexicon is a seed; entity coverage bounded and auditable.

## 8. Availability and reproducibility

- Repository tag, wheel hash, Docker image digest.
- Commands to reproduce the demo and the deterministic report check.
- Gold artefacts and the exact corpus hash; instructions to re-fetch abstracts.

## Figures and tables (planned)

| Item | Content | Source | Status |
| --- | --- | --- | --- |
| Fig. 1 | pipeline diagram | `docs/ARCHITECTURE.md` | ready |
| Fig. 2 | sampling frame composition | `gold/v2/sampling_frame.json` | ready |
| Fig. 3 | rubric distribution per entity | `ntx report` output | ready (demo) |
| Table 1 | entity classes and examples | `src/ntx/data/ontology.json` | ready |
| Table 2 | gold v2 agreement (κ) | `ntx gold agreement` | TBD |
| Table 3 | main metrics with 95% CI | `ntx eval --bootstrap` | TBD |
| Table 4 | ablations | `docs/METHOD.md` §6 | TBD |
| Table 5 | error taxonomy counts | adjudicated gold | TBD |

## Submission checklist

- [ ] Gold v2 adjudicated and validated (0 errors)
- [ ] All TBD tables filled from artefacts, not from memory
- [ ] Every number traceable to a committed file or a documented command
- [ ] Reporting rules in `docs/METHOD.md` §9 followed
- [ ] Data governance checklist in `docs/DATA_GOVERNANCE.md` complete
- [ ] Code and docs licences stated; DOI minted for the release
