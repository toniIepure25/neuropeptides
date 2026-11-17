# Candidate triage and prediction ledger

This document defines the closed loop: a candidate is recorded, an experiment is
registered with a directional prediction **before** the result, the result is
appended, and the prediction is scored. The ledger is a local audit trail, not a
clinical trial registry and not evidence that a candidate works. Precursor
cleavage and analog generation are a separate explicit layer
(`docs/GENERATION.md`); generated sequences still have to pass through this
ledger.

## 1. What a candidate is

A candidate is a peptide sequence supplied by the user or imported from a file.
The system does not generate sequences in this release. Import is transactional
and append-only; the same sequence, modification list and target always receive
the same `C-...` id, while name and provenance remain metadata.

```json
{"name": "example peptide", "sequence": "ACDE", "target_entity_id": "NP:OXT", "modifications": ["N-terminal acetylation"], "provenance": "user-supplied", "notes": "synthetic example, not a real candidate"}
```

```powershell
ntx candidate import --db build/demo.db --file candidates.jsonl --dry-run
ntx candidate import --db build/demo.db --file candidates.jsonl
ntx candidate list --db build/demo.db
ntx candidate show --db build/demo.db --id C-...
```

Validation: canonical 20 residues only, length 2–200, no internal whitespace,
known `target_entity_id` if supplied, and no duplicate id. `--dry-run` validates
without writing.

### Heuristic properties

`compute_properties` reports length, monoisotopic molecular weight, net charge at
pH 7.4, GRAVY, aromatic and cysteine counts, and simple protease motifs. Every
object carries `"heuristic": true` and
`"modifications_applied_to_properties": false`.

These are residue-table descriptors, not measurements. They do not predict
activity, potency, stability, brain exposure or permeability. A molecular weight
below a rule-of-thumb cutoff is not evidence of BBB penetration.

## 2. Registering a prediction

The hypothesis must exist in the current database (`ntx discover --json`). The
experiment stores a snapshot of that hypothesis, so later changes to the discovery
algorithm do not rewrite history.

```powershell
ntx discover --db build/demo.db --json
ntx ledger register --db build/demo.db `
  --hypothesis H-... `
  --design rct `
  --assay "verbal learning" `
  --primary-outcome memory `
  --prediction increase `
  --probability 0.7 `
  --n-planned 40 `
  --preregistration "doi:10.0000/example"
```

Rules:

- `prediction` is `increase` or `decrease`; the probability is confidence in that
  direction and must be in `[0.5, 1.0)`.
- `design`, `assay` and `primary_outcome` are required non-empty strings.
- `--candidate` is optional and, when present, must exist.
- `n_planned` is a recorded value. Attach `--power power.json` (or a manifest
  `power` object) to record a two-arm worksheet; the ledger recomputes it, stores
  it, and reports whether `n_planned` meets the requirement. A shortfall is a
  warning, not a block.
- The same registration can be supplied as a versioned manifest with
  `--file registration.json`; unknown keys are rejected and `--dry-run` writes
  nothing (`docs/REGISTRATION.md`).
- One prediction per experiment. The row is write-once.

## 3. Recording an outcome

```powershell
ntx ledger outcome --db build/demo.db `
  --experiment E-... `
  --observed increase `
  --effect-size 0.3 --ci-low 0.1 --ci-high 0.5 `
  --n 40 --qc pass --source-id registry:study-123
```

Outcomes are append-only. A correction is a **new** outcome row; the latest row
(by timestamp and insertion order) is scored and exported. All revisions remain in
the database and in `ledger export`.

Validation: `observed` is one of `increase`, `decrease`, `none`, `mixed`,
`unclear`; `qc` is `pass`, `warn`, `fail`; CI bounds must be supplied together and
ordered; `n` must be non-negative. Effect-size sign is not forced to match the
direction because scales and coding differ across assays.

`source_id` is an optional external identity label. When supplied, it must be
unique within an experiment, so a repeated import cannot masquerade as two
results. The ledger does not verify the source.

Batches can be imported from JSON or JSONL with `ntx ledger import-outcomes`.
Imports are transactional and idempotent: identical repeats skip, conflicting
repeats fail, and all experiment ids are checked before any insert. See
`docs/REGISTRATION.md`.

## 4. Scoring

```powershell
ntx ledger status --db build/demo.db
ntx ledger score  --db build/demo.db --json
ntx ledger calibration --db build/demo.db
ntx ledger export --db build/demo.db --out reports/ledger.json
```

Only the latest outcome per experiment is used. An observation is **eligible**
when:

- `qc != fail`, and
- `observed` is `increase` or `decrease`.

For each eligible experiment, let `p` be the registered probability of the
predicted direction and `y = 1` when the observed direction equals the prediction,
`0` when it is opposite. Then:

```text
Brier       = mean((p - y)^2)
Brier skill = 1 - Brier / 0.25
accuracy    = mean(y)
```

`0.25` is the Brier score of always predicting `p = 0.5`, so skill is relative to
an uninformative forecaster. Accuracy is reported with a 95% Wilson interval.

Exclusions are counted, never imputed:

- `pending`: no outcome yet;
- `excluded`: QC fail, or a non-directional observation (`none`, `mixed`,
  `unclear`).

With fewer than 10 eligible observations, `underpowered` is true and the output
says the result is descriptive only. Per-kind and per-design metrics are printed
only when that group has at least 3 eligible observations and 2 clusters, and are
still descriptive. A small ledger is not calibration evidence.

`ntx ledger calibration` adds a cluster-aware reliability table over the same
eligible rows: equal-width probability bins, mean predicted vs observed agreement,
ECE/MCE, the binned Murphy decomposition and a hypothesis-cluster bootstrap when
there are at least 10 rows and 10 clusters. It also reports subgroup summaries,
CI precision counts, correction counts, missingness by design and one-at-a-time
sensitivity analyses. `--out` writes canonical JSON with a content hash;
`--html` writes a self-contained reliability report. Empty and sparse bins are
reported. The output is always `descriptive_only`; it does not estimate a
replication probability or turn a small or clustered ledger into calibration
evidence (`docs/CALIBRATION.md`).

## 5. Trust boundary

The ledger can prove that a prediction row exists and that it was not edited
through the supported commands (SQLite triggers reject `UPDATE` and `DELETE`).
It **cannot** prove that the prediction was made before the experiment was run.
The timestamp is local, and a user can enter an old experiment afterwards.

Mitigations are procedural and technical: record the preregistration identifier,
freeze the database before unblinding, export the ledger with its content hash
before looking at the result, and write a checkpoint of that export. Export schema
v3 covers precursors, candidates, lineage, experiments (including power worksheets),
predictions and outcomes (including `source_id`); it excludes only `exported_at`, so
two exports of the same ledger share a hash. Registration timestamps and `source_id`
values remain local claims, not trusted external proof.

`ntx ledger checkpoint` writes a hash-chained snapshot of the ledger content hash
and `ntx ledger verify-checkpoints` detects edits, missing sequences, reordering and
a current ledger that differs from the latest snapshot. A checkpoint is not a
trusted timestamp or a signature: an attacker who can rewrite the whole checkpoint
directory can recompute the chain. Its value depends on anchoring an earlier hash
outside that directory, for example in a dated archive or a release artefact
(`docs/INTEGRITY.md`).

`ntx verify` separately compares document metadata with Crossref/PubMed and reports
retraction drift, corrections, metadata mismatches, unverifiable records and
unavailable sources. It is read-only: it does not change a retraction status,
delete a claim or downgrade evidence automatically. Source checks and checkpoints
answer different questions and do not replace each other.

## 6. What this release does not do

- Invent, dock or rank molecules; generation is limited to explicit precursor
  cleavage and truncation/alanine-scan operations (`docs/GENERATION.md`).
- Predict activity, potency, efficacy, BBB penetration or safety.
- Compute power for paired, cluster, time-to-event, ordinal or adaptive designs;
  only simple two-arm parallel mean/proportion designs are supported.
- Provide a trusted registry timestamp or prove that a preregistration existed
  before an experiment.
- Model dose/route.
- Provide multi-user access control or a remote registry.
- Claim that a descriptive reliability table implies calibrated probabilities or a
  replication risk.

Power worksheets, registration manifests, transactional outcome import and the
descriptive calibration report are implemented in v0.9/v0.10
(`docs/REGISTRATION.md`, `docs/CALIBRATION.md`). Source verification and checkpoint
chains are implemented in v0.12 (`docs/INTEGRITY.md`). An optional PMC JATS
full-text layer is implemented in v0.13 and reconciled at claim level in v0.14
(`docs/FULLTEXT.md`); publisher PDF verification, trusted registration time and
broader design support remain gaps.
