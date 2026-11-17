# Method specification (v3)

This document states what the system measures, how it is scored, and which
claims are supported by the current evidence. It is deliberately explicit about
what is *not* yet measured; the manuscript must not outrun this file.

## 1. Scope

The system extracts, normalizes and appraises **published claims** about
neuropeptides (`NP`), neurotrophic factors (`NTF`) and neurotropic substances
(`NTS`) from titles and abstracts indexed by Europe PMC. The claim layer does not
model dose, route or comparator, and it is not a clinical decision tool. A
separate, explicitly hypothetical mechanism layer (section 11) propagates signs
through a curated and literature-derived graph; it is not a kinetic model and
does not establish causal mechanism.

## 2. Pipeline

1. **Retrieval** — Europe PMC `resultType=core` search, cached per URL hash.
2. **Detection** — curated gazetteer, longest match, token boundaries, acronym
   expansion requirement; Europe PMC annotations are candidates only and are
   re-mapped to curated IDs.
3. **Extraction** — sentence segmentation; clause segmentation for the direction
   cue and the evidence span; direction (`increase`, `decrease`, `none`, `mixed`,
   `unclear`), outcome, species, design, n, p-value, hedge, negation, entity role
   (`intervention`/`comparator`/`mechanism`), abstract section and confidence
   reasons, each anchored to an exact character span. An opt-in resolver links
   definite anaphora to the single prior entity and abstains when ambiguous.
4. **Appraisal** — design from publication types, retraction/concern status,
   bias flags, additive rubric with printed reasons.
5. **Storage** — SQLite with schema versioning; provenance invariant checked
   before any report is rendered.
6. **Reporting** — per-entity Markdown and self-contained HTML briefs.

## 3. Benchmark design

Two gold sets exist:

| Set | Size | Annotators | Status |
| --- | --- | --- | --- |
| `gold/pilot.jsonl` | 6 documents, 17 claims | 1 | claims only; **rules-v1, historical** (abstracts not redistributed) |
| `gold/v2/` | 119-document frozen frame | model draft only | **human annotation pending** |

Gold v2 is a stratified frame: 5 compounds × 3 evidence strata, with queries,
retrieval timestamp and per-document content hashes frozen in
`gold/v2/sampling_frame.json`. The annotation schema, agreement statistics and
adjudication procedure are specified in `gold/PROTOCOL_V2.md`.

## 4. Metrics

- **Mention span P/R/F1** — exact character offsets.
- **Normalized-ID P/R/F1** — span plus curated entity ID.
- **Normalization accuracy** — share of matched spans with the correct ID.
- **Claim P/R/F1 (strict)** — `(doc_id, entity_id, direction, outcome, span start)`.
- **Claim P/R/F1 (role-strict)** — the same key plus `role`. Reported with
  `role_gold_coverage`: a role score is ignored when gold has no explicit roles.
- **Role accuracy on matched claims** — share of strict matches whose role sets
  intersect, plus `role_mismatches` examples.
- **Claim error attribution** — every strict FP/FN is classified by its nearest
  counterpart on `(doc, entity)`: `entity_mismatch`, `outcome_mismatch`,
  `direction_mismatch`, `span_mismatch`, `sentence_mismatch`.
- **Claim P/R/F1 (direction-relaxed)** — `(doc_id, entity_id, direction)`.
- **Provenance errors** — must be 0; a nonzero count invalidates the run.
- **Agreement** — Cohen's κ per field on double-annotated documents, with raw
  agreement and n.
- **Uncertainty** — document-level bootstrap 95% percentile intervals for every
  P/R/F1 (`ntx eval --bootstrap`), because claims within an abstract are not
  independent.
- **Gold integrity** — before metrics are trusted, each gold claim sentence and
  mention span is checked against the stored document text. The report exposes
  `gold_span_errors` and `gold_missing_documents`; `ntx eval --strict-gold`
  exits 1 when either is non-zero.
- **Manual audit** — until gold exists, `ntx audit sample --n 40` draws a
  deterministic role/design-stratified worksheet of model claims with sentence
  and evidence span, plus a blank verdict template. `ntx audit score` joins the
  completed `support`/`refute`/`unclear` verdicts to claims and reports precision
  with a 95% Wilson interval, overall and by role/design. `unclear` and blank
  rows are excluded from the denominator and reported; missing claim ids are
  counted. This is a descriptive audit of model output, not a replacement for
  the gold benchmark.

## 5. Decision gates

| Gate | Threshold | Current |
| --- | --- | --- |
| Span validity | 100% | met on demo (0 errors) |
| Mention span F1 | ≥ 0.85 | unmeasured (no mention gold) |
| ID accuracy | ≥ 0.85 | unmeasured |
| Claim F1 (strict) | ≥ 0.70 | 0.552 on the rules-v1 pilot (historical); rules-v2 unmeasured |
| Direction κ | ≥ 0.70 | not computable (single annotator) |
| Outcome κ | ≥ 0.60 | not computable |
| Retraction recall | 1.00 | unmeasured at scale |

The pilot number is reported with its n and must not be presented as a
validation result. It was measured with rules-v1 on abstracts that are not
redistributed; the current extractor is rules-v2. No v2 gold metric may be
quoted until `gold/v2/gold.jsonl` validates with zero errors.

## 6. Ablation plan and flags

The extractor exposes deterministic `FeatureFlags` (`clause_direction`,
`comparator_role`, `section_filter`, `resolve_coref`, `use_annotations`). The
paired ablation runner (`ntx ablate`) reports delta-F1 against the baseline on
the same documents and refuses a configuration that breaks provenance. Results
are void when gold documents are missing from the corpus; the runner reports
them explicitly.

Implemented ablations:

1. Gazetteer only vs gazetteer + Europe PMC annotation candidates.
2. Longest-match vs longest-match + acronym expansion (isolates the abstention
   rule's cost).
3. Sentence-level direction vs clause segmentation (multi-finding sentences).
4. With and without Background/Objective/Methods sentence filtering.
5. With and without contrast-aware negation ("no effect at 24 h but reduced at
   3 h").
6. With and without comparator-role detection.
7. With and without opt-in coreference resolution.
8. Rubric variants: without retraction flag (to show its weight is not cosmetic).

Each ablation is reported as a paired comparison on the same documents with
bootstrap CIs, never as a single unpaired F1.

## 7. Calibration plan

The rubric outputs `low`/`moderate`/`high` evidence strength, not a probability.
Calibration requires an external outcome per claim (independent replication,
retraction, or a registered replication attempt). No such labels exist yet, so
the rubric must be described as a transparent triage heuristic. When labels are
available, report a reliability diagram and Brier score per rubric bin before any
probability language is used. This is separate from the descriptive calibration of
directional ledger predictions in §15; that report does not calibrate the rubric.

## 8. Error taxonomy (pilot, n = 6)

1. Implicit subject / coreference — recall loss by design; opt-in resolver added,
   precision unmeasured.
2. Multiple findings per sentence — clause-aware direction and evidence spans
   added; claim granularity still sentence-level.
3. Outcome granularity — hierarchical outcomes with partial credit.
4. Background statements mistaken for own findings — partially filtered by
   section labels; no gold check yet.
5. Comparator role ("compared with oxytocin alone") — now labelled
   `role=comparator` and excluded from contradiction counts; effect on metrics
   unmeasured.

Each item maps to an ablation above; none is silently patched in the gold set.

## 9. Evidence synthesis

`ntx synthesize` aggregates claims per (entity, outcome). Each document counts
once, weighted by the prior attached to its strongest design
(`meta_analysis` 1.0 … `in_vitro` 0.25). Comparator claims are excluded by
default; `neutral` (none/mixed/unclear) is reported but not pooled into the
directional proportion. The interval is a document-level bootstrap, and a row is
`fragile` when the interval crosses 0.5 or has too few documents. The weights are
priors, not calibrated probabilities, and the command says so in its output.

## 10. Reporting rules

- Every metric is reported with n, the gold set version and 95% CI.
- Negative and null results are reported, including strata where the system
  performs poorly.
- Thresholds in §5 are fixed before the v2 gold is unblinded; changing one
  requires a changelog entry explaining why.
- Model-drafted annotations are labelled `draft`/`silver` in every table.

## 11. Mechanistic layer and qualitative simulation

The goal is to make mechanistic assumptions **explicit and inspectable**, not to
discover a validated mechanism. The graph has two edge sources:

1. A curated prior (`src/ntx/data/mechanisms.json`): ligand→receptor,
   receptor→pathway and pathway→process edges, each with a confidence and a
   short reference label. These are textbook-level assumptions, not citations,
   and every output that uses them says so.
2. Extracted edges: a sentence must contain a curated entity alias and a
   mechanism alias, both inside one clause, plus a direction cue
   (`activate/stimulate/increase/...` or `inhibit/block/decrease/...`). Negation
   immediately before the cue vetoes the edge; a bare `via/through/associated`
   cue becomes a sign-0 edge that is displayed but never simulated. Mechanism
   names that overlap an entity mention (for example "substance P receptor") are
   skipped, because naming a receptor is not evidence that the ligand acted.
   Mechanism→outcome edges inherit the claim direction only when the mechanism
   occurs in that claim's sentence. They also retain the claim's entity, so a
   path is never stitched together from an oxytocin sentence and a BDNF outcome.
   Every extracted edge retains doc ids.

Simulation propagates signs over simple paths up to depth 5. A path's sign is the
product of its edge signs and its confidence is the minimum edge confidence.
Per outcome, the baseline score is the confidence-weighted mean sign. Blocking a
node removes every path through it; the reported delta is
`perturbed - baseline`, with `increase`/`decrease` only outside a fixed
ambiguity band. The output is a **direction**, never a magnitude, concentration
or effect size.

`ntx mechanism check` holds out 20% of documents (deterministic SHA-1 bucket,
configurable) and builds the graph without them. It then compares the predicted
baseline sign with each held-out claim direction and reports agreement,
disagreement and uncovered claims. This is a weak structural check, not
validation of the mechanism or of causality. `--holdout 0` is explicitly
in-sample and descriptive.

`ntx mechanism enrich` is the only online path: it fetches UniProtKB metadata
for a mechanism accession into the normal HTTP cache. It adds no edges
automatically and is opt-in (`--offline` works from cache). The core commands
remain offline.

Known limits: rules miss implicit mechanisms and can misattribute coordinated
mentions; the prior encodes contested biology; outcomes come from the claim
extractor; a graph with no path to an outcome is a coverage limit, not evidence
of no mechanism.

## 12. Candidate ledger and directional calibration

Candidates are user-supplied peptide sequences. Import validates canonical
residues, length 2–200 and optional ontology targets, computes residue-table
descriptors (molecular weight, net charge at pH 7.4, GRAVY, aromatic/cysteine
counts, simple protease motifs), and stores them with a content-derived id.
Descriptors are explicitly heuristic and are not activity, stability or
permeability predictions.

Each registered experiment stores a snapshot of a discovery hypothesis, the
design, assay, primary outcome, planned n and optional preregistration id. A
prediction is one direction (`increase`/`decrease`) with probability
`p in [0.5, 1.0)`, written once. Outcomes are append-only; a correction is a new
row and the latest row is used. The score is a directional Brier score over
eligible observations: `y=1` when the observed direction equals the predicted
direction, `0` when opposite, and

`Brier = mean((p - y)^2)`, `skill = 1 - Brier / 0.25`.

`qc=fail`, `none`, `mixed` and `unclear` are excluded and counted. Overall
results with fewer than 10 eligible observations are marked underpowered; group
results require at least 3 observations and remain descriptive. The ledger cannot
prove that a prediction preceded the experiment; timestamps, optional
preregistration ids and an export hash are audit aids, not proof. Power
worksheets are treated separately in §14; automatic outcome extraction and
docking remain out of scope.

## 13. Precursor cleavage and analog enumeration

Generation is a separate explicit layer, never an automatic step in hypothesis
mining. A precursor is a user-supplied sequence (2–10,000 canonical residues,
optional UniProt label). Cleavage uses one named rule (trypsin, chymotrypsin,
Lys-C, Arg-C or Glu-C): cut after the target residue, except before proline.
`missed_cleavages` enumerates spans joining up to N+1 adjacent segments. It is a
combinatorial allowance, not a kinetic or probabilistic digestion model; pH,
time, accessibility, signal peptides and PTMs are not modelled.

Identical peptide sequences at multiple sites collapse to one candidate; all
observed sites and their count are retained in lineage parameters. Candidate
analogs are limited to N/C truncations (keeping at least two residues) and a
single alanine substitution per position. Analogs inherit the parent's target but
not its modifications or hypothesis association. Every generated row records its
source, operation, span, parameters and a deterministic lineage id. Expansion
caps fail closed rather than silently truncating.

Generated sequences are hypothesis sources. No ranking, docking, permeability,
stability, immunogenicity or activity prediction is performed, and no claim of
novelty or synthetic accessibility is made. Ledger export schema v3 includes
precursors, lineage, power worksheets and outcome source ids in the content hash.

## 14. Registration, power and external outcomes

A registration stores the experiment, a write-once directional prediction and,
optionally, a canonical power worksheet. The worksheet supports only simple
two-arm parallel designs with equal allocation: a mean difference (Cohen's d) or
a risk difference between two proportions, under a normal approximation with no
continuity correction. It records alpha, one- or two-sided testing, target power,
dropout and every input; the ledger recomputes it and rejects a stale or tampered
sheet. Dropout is inflated by division, not a missing-data model. The worksheet is
a design aid, not evidence that the assumed effect exists.

`n_planned` remains a separate recorded choice. A shortfall against the computed
requirement is reported as `power_check.meets_required = false`, never as a write
failure. Registration can be entered as flags or as a versioned JSON manifest;
unknown fields are rejected, and `--dry-run` validates without writing. The
manifest is not archived as a file; its validated fields and worksheet are stored.

External outcomes can be imported in one transaction from JSON or JSONL. Each
record requires a non-empty `source_id`, unique within an experiment. An identical
re-import is a skip; the same source id with different fields is an error, and a
correction needs a new id. All experiment ids are preflighted before any insert.
The source id is an unverified label, not a registry lookup. Imported outcomes are
scored exactly like manually entered rows, with the same exclusion rules.

## 15. Descriptive calibration

The calibration report uses the same eligible rows as the directional Brier score:
latest outcome per experiment, QC pass/warn, and a decisive observed direction.
`p` is the registered probability of the predicted direction and `y=1` when the
observed direction agrees. Probabilities are split into equal-width bins between
0.5 and 1.0; empty bins are reported and bins with fewer than three rows are
marked sparse.

Rows are clustered by hypothesis by default. The deterministic percentile
bootstrap resamples whole hypotheses, not rows, using 500 resamples and the same
bin edges; intervals are reported only when there are at least 10 rows and 10
clusters. `--cluster experiment` restores independent-experiment resampling for
comparison. A single hypothesis with many experiments is one cluster, not many
independent observations.

Reported metrics are Brier, skill versus 0.25, a cluster-bootstrap interval for
Brier/skill/ECE, accuracy with a 95% Wilson interval, ECE, MCE and the binned
Murphy decomposition `Brier = reliability - resolution + uncertainty`.
Subgroups by hypothesis kind and design show metrics only with at least 3 rows and
2 clusters; they are descriptive and are not tested for differences.

The primary analysis is fixed in advance. Sensitivity variants change one choice
at a time: earliest instead of latest outcome, exclude `qc=warn`, and restrict to
outcomes whose confidence interval is consistent with the observed direction.
Each variant reports its delta from the primary analysis; there are no p-values
and no "best" specification. The report also counts CI consistency classes,
corrections, and missingness by design.

The report is always `descriptive_only`. Fewer than 10 eligible observations or
10 clusters sets `underpowered` with explicit reasons; even above it, the ledger
is not a random sample and may be selectively reported. No rubric probability,
replication probability or treatment effect is estimated, and the output never
licenses the word "calibrated". JSON and a self-contained HTML report can be
archived with a content hash; the hash is not a signature.

## 16. Source verification and ledger checkpoints

`ntx verify` re-reads document metadata from Crossref (DOI first) or PubMed (PMID
fallback) through the cached HTTPS transport. It compares normalized titles and
years, maps retraction/concern/correction notices to a severity order, and reports
one of `verified`, `metadata_mismatch`, `retraction_drift`, `local_retraction`,
`correction`, `unverifiable` or `unavailable`. A local retraction is never
downgraded because a registry response omits a notice. `--fail-on-drift` exits
non-zero only for metadata mismatch or a newly detected retraction/concern. The
report is a snapshot with a content hash; it does not alter stored evidence.

`ntx ledger checkpoint` records the ledger export content hash, a sequence number
and the previous checkpoint's content hash. `ntx ledger verify-checkpoints`
validates the checkpoint hashes, continuous sequence, previous links and the current
ledger hash. This detects edits, gaps, reordering and post-checkpoint ledger
changes. It does not establish a trusted time, does not prove that a prediction
preceded an experiment, and is not a signature; an attacker who rewrites the whole
checkpoint directory can recompute the chain unless an earlier hash is anchored
externally (`docs/INTEGRITY.md`).

## 17. Open-access full-text evidence

`ntx fulltext` is an opt-in layer for documents with a PMC identifier and an
available JATS body. `parse_jats` rejects `DOCTYPE`/`ENTITY`, reads only `body`,
normalizes inline tags, joins paragraphs with `\n\n`, and records a start offset
for each section. Unknown subsections inherit the nearest known parent; top-level
unknowns become `other`.

`ntx fulltext fetch` stores one immutable snapshot: normalized text, section
offsets, source URL, licence, fetch time and SHA-256. It skips documents without a
PMCID or with an existing snapshot and reports `unavailable`, `too_large`,
`unsafe_xml`, `invalid_xml` or `empty` separately. `ntx fulltext extract` detects
mentions over the full text, passes JATS section offsets to the claim extractor,
and keeps only Results, Discussion and Conclusions claims. Every claim stores the
snapshot `text_hash`; `ntx check` verifies the exact span against that snapshot.

`ntx fulltext report` compares abstract and full-text tuples:

- `corroborated` — same entity, outcome and direction;
- `fulltext_only` — no matching abstract entity/outcome;
- `abstract_only` — no matching full-text tuple;
- `direction_conflict` — same entity/outcome, opposite direction.

The report is descriptive. It does not prove that the full-text sentence is true,
that the abstract is faithful, or that the underlying study is valid. The layer is
limited to PMC open-access JATS, does not read publisher PDFs comprehensively, and
does not feed default reports, discovery, calibration or ledger exports. Database
schema v8 adds quantitative effect columns to v7's `document_texts` and
`fulltext_claims`; ledger export v3 is unchanged (`docs/FULLTEXT.md`).

## 18. Quantitative effects

Effect extraction is deliberately conservative and rule-based. A claim carries an
effect only when the sentence has an explicit label: SMD/Cohen's d/Hedges' g, mean
difference, odds ratio, risk ratio, hazard ratio, beta, correlation/Pearson's r,
or a signed percent change. Unlabelled numbers are ignored. The parser records the
effect value, an optional 95% CI, and explicit comparator, dose, route, timepoint
and population quotes; every quote is validated against the claim sentence.

The effect type and context are heuristic annotations, not verified study
parameters. Effect size is compared in reconciliation only when both layers use a
comparable type and their CIs do not overlap; that is a descriptive discrepancy
signal, not proof of error. Evaluation reports type accuracy on matched claims,
value MAE, CI overlap and gold/prediction coverage, and withholds effect metrics
when no gold effects exist.

## 19. Version history

- **v1** — sentence-level claims, single direction per (sentence, entity).
- **v2** — gold schema v2, bootstrap intervals, sampling frame, error taxonomy.
- **v3 (0.3.0)** — clause-scoped claims with role, evidence span and section;
  paired feature-flag ablations with delta-F1 intervals; design-weighted
  synthesis; read-only local API. Gold metrics remain rules-v1 historical until
  the 30-document human v2 gold is adjudicated.
- **v4 (0.4.0)** — role-strict evaluation with gold-role coverage and
  role-mismatch examples; deterministic claim error attribution; JSON-only
  machine output; offline draft replay and draft v0.3 (role/section/evidence);
  field-level adjudication skeletons (`gold conflicts`); deterministic audit
  sampling (`audit sample`); full local CI parity. No human gold metric yet.
- **v5 (0.5.0)** — gold integrity gates (`gold_span_errors`,
  `gold_missing_documents`, `--strict-gold`) and the sample → verdict → score
  audit loop with 95% Wilson intervals. Audit precision is descriptive model
  output precision, not a gold benchmark.
- **v6 (0.6.0)** — mechanist graph (curated prior + extracted entity→mechanism
  and mechanism→outcome edges with doc provenance), qualitative block simulation
  with explicit non-kinetic framing, deterministic holdout sign check, graph
  export, and opt-in UniProt metadata enrichment. No mechanism is claimed as
  validated.
- **v7 (0.7.0)** — candidate import with heuristic descriptors; append-only
  experiment, prediction and outcome ledger; directional Brier score with counted
  exclusions, Wilson accuracy interval and an underpowered flag. No molecule
  generation or activity prediction; no calibration claim from small ledgers.
- **v8 (0.8.0)** — user-supplied precursor records; parameterised cleavage with
  five named enzyme rules and missed-cleavage enumeration; N/C truncation and
  single alanine-scan analogs; append-only lineage; ledger export v2. Generated
  sequences are not ranked or predicted; cleavage is not an in-vivo claim.
- **v9 (0.9.0)** — two-arm mean/proportion power worksheets with recomputation
  and shortfall warnings; versioned registration manifests and dry-run; schema v6
  with `power_json` and outcome `source_id`; idempotent external outcome import;
  ledger export v3. No support for paired, cluster, time-to-event or adaptive
  designs, and no verification of external source ids.
- **v10 (0.10.0)** — descriptive calibration report with equal-width reliability
  bins, Brier skill, ECE/MCE, Murphy decomposition and deterministic bootstrap
  intervals. Always descriptive-only; under 10 eligible observations it is
  underpowered. No schema or export change; no calibration claim.
- **v11 (0.11.0)** — cluster-aware calibration: hypothesis-level bootstrap,
  explicit cluster shortfall, subgroup summaries, CI precision counts, correction
  and missingness audit, one-at-a-time sensitivity analyses, canonical JSON with
  content hash and a self-contained HTML reliability report. Report schema v2;
  database v6 and ledger export v3 unchanged. No hierarchical model or trusted
  timestamps.
- **v12 (0.12.0)** — read-only Crossref/PubMed source verification with explicit
  drift statuses and `--fail-on-drift`; hash-chained ledger checkpoints with
  sequence, previous-link and current-ledger verification. No database or export
  schema change; no trusted timestamp, signature or full-text validation.
- **v13 (0.13.0)** — optional open-access PMC JATS layer with immutable local
  snapshots, section-aware full-text claims, exact-span provenance and an
  abstract/full-text corroboration report. Schema v7; ledger export v3 unchanged.
  Limited to PMC open-access XML; no publisher PDFs, semantic equivalence or truth
  claims.
- **v14 (0.14.0)** — claim-level reconciliation replaces tuple counting:
  entity/outcome groups, explicit reasons, severity, exact-span references,
  `--entity`, `--fail-on` and optional `report --with-fulltext` sections. Report
  schema v2; database v7 and ledger export v3 unchanged. No automatic promotion of
  full-text claims into reports, discovery or calibration.
- **v15 (0.15.0)** — conservative quantitative extraction (type, size, CI and
  context with exact quotes), schema v8, effect-aware reconciliation/evaluation,
  quality gates, a self-contained dashboard, DB backup/verify/restore, `doctor`,
  and token/Host-guarded API access. Effect fields are heuristic and effect-size
  comparison is descriptive only; no human effect gold exists yet.
- **v16 (0.16.0)** — effect rigor and operational safety: CI ownership is bounded
  to the nearest effect, quant payloads are schema-versioned and validated,
  multi-effect claims are marked ambiguous rather than silently reduced, effect
  MAE is same-type only, effect audit sampling adds a separate human verdict,
  quality payload v2 exposes thresholds/ambiguous counts and `--require-gold`,
  backups are copy-verified and refuse silent overwrite, restores re-verify
  metadata, and the read-only API adds security headers plus a minimum token
  length for non-loopback binds. Report schema v4; database v8 unchanged.
- **v17 (0.17.0)** — public-release hardening: public gold is text-free (labels,
  exact spans and SHA-256 hashes) with `ntx gold redact` / `ntx gold hydrate`;
  evaluation refuses redacted input; the bundled demo is restricted to explicit
  CC BY records with attribution; release metadata, community files, PEP 561
  typing and a tag-driven release workflow are added. Annotation schema remains
  v2 (new optional fields); database v8, report v4, quality v2 and ledger v3 are
  unchanged. No human gold, new accuracy claim or clinical recommendation is
  introduced.
