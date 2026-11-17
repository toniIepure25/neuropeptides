# Registration, sample size and external outcomes

This layer makes the design assumptions behind `n_planned` explicit and lets a
study team import structured results without weakening the append-only ledger.
It does **not** establish that the assumed effect is real, that registration
happened before the experiment, or that an imported result is authentic.

## 1. Power worksheets

```powershell
ntx power --test mean --effect-size 0.5 --alpha 0.05 --target-power 0.8 `
  --dropout 0.1 --sided two --out power.json

ntx power --test proportion --p1 0.2 --p2 0.4 --alpha 0.05 `
  --target-power 0.8 --dropout 0.1 --json
```

The worksheet is for a **two-arm parallel design with equal allocation**. It
uses a normal approximation and no continuity correction.

For a mean difference with Cohen's `d`:

```text
n/arm = 2 * ((z(1 - alpha/s) + z(target_power)) / d)^2
```

For two proportions with `pbar = (p1 + p2) / 2`:

```text
n/arm = ( z(1 - alpha/s) * sqrt(2*pbar*(1-pbar))
          + z(target_power) * sqrt(p1*(1-p1) + p2*(1-p2)) )^2 / (p2-p1)^2
```

`s = 2` for a two-sided test and `1` for one-sided. `n/arm` is rounded up.
Dropout is inflated by `ceil(n/arm / (1 - dropout))`.

Reference values:

| Design | Inputs | n/arm | total | with 10% dropout |
| --- | --- | ---: | ---: | ---: |
| mean | d=0.5, two-sided, alpha=.05, power=.8 | 63 | 126 | 70/arm, 140 |
| proportion | p1=.2, p2=.4, two-sided | 82 | 164 | 92/arm, 184 |
| mean | d=0.5, one-sided | 50 | 100 | 56/arm, 112 |

The JSON worksheet has `schema: "ntx-power-v1"` and a deterministic `power_id`.
It records every input, the method, assumptions and caveats. The ledger
**recomputes** the worksheet before accepting it; changing `n_per_arm` or
`power_id` by hand makes registration fail.

Limits:

- No paired, crossover, cluster, time-to-event, ordinal or adaptive designs.
- No continuity correction, stratification, covariate adjustment or missing-data
  mechanism beyond simple dropout division.
- The effect size and proportions must be supplied by the user; the system does
  not estimate them from the corpus or from previous outcomes.
- A power worksheet is a design aid, not evidence that the effect exists.

## 2. Registration manifests

Flag-based registration remains available. A versioned manifest is easier to
review, archive and replay:

```json
{
  "schema": "ntx-registration-v1",
  "hypothesis_id": "H-...",
  "candidate_id": "C-...",
  "design": "rct",
  "assay": "verbal learning",
  "primary_outcome": "memory",
  "prediction": "increase",
  "probability": 0.7,
  "n_planned": 140,
  "preregistration": "doi:10.0000/example",
  "rationale": "effect size from prior work, with explicit assumptions",
  "power": {
    "schema": "ntx-power-v1",
    "power_id": "PW-...",
    "...": "remaining worksheet fields"
  }
}
```

The `power` value must be the complete worksheet object emitted by
`ntx power --out`; the ellipsis above is illustrative and would be rejected. Omit
`power` when no worksheet is attached.

```powershell
ntx ledger register --db build/demo.db --file registration.json --dry-run --json
ntx ledger register --db build/demo.db --file registration.json
```

Rules:

- `schema` and the fields shown as required are mandatory; unknown keys are
  rejected so a typo cannot silently disappear.
- `candidate_id`, `n_planned`, `preregistration`, `rationale` and `power` are
  optional.
- `--file` cannot be combined with individual registration flags or `--power`.
- Flag-based registration can attach a worksheet with `--power power.json`.
- `--dry-run` validates the hypothesis, candidate, prediction and worksheet but
  writes nothing.
- The experiment stores the validated worksheet as `power`; export includes it.

If a worksheet is present, the result contains a `power_check`:

```json
{
  "provided": true,
  "required_total": 140,
  "n_planned": 140,
  "meets_required": true
}
```

`n_planned` below the requirement is a **warning**, not a block. A pilot,
feasibility constraint or deliberate underpowered study remains possible, but it
must not be described as adequately powered. With no worksheet,
`power_check.provided` is false and `n_planned` is recorded only.

The manifest file itself is not archived. Its validated fields and worksheet are
stored in the ledger; the exact file can be regenerated from them. Registration
timestamps remain local and are not proof that registration preceded the
experiment.

## 3. External outcome import

```powershell
ntx ledger import-outcomes --db build/demo.db --file results.jsonl --dry-run
ntx ledger import-outcomes --db build/demo.db --file results.jsonl
```

Accepted forms: a JSON array, JSONL, or a wrapper:

```json
{"schema": "ntx-outcomes-v1", "outcomes": [ ... ]}
```

Each record:

```json
{
  "experiment_id": "E-...",
  "source_id": "registry:study-123:primary",
  "observed": "increase",
  "effect_size": 0.3,
  "ci_low": 0.1,
  "ci_high": 0.5,
  "n": 40,
  "qc": "pass",
  "note": "primary result"
}
```

Rules:

- `experiment_id`, `source_id` and `observed` are required.
- `source_id` is an external identity label, not a verified registry lookup. It
  must be non-empty and unique within an experiment.
- Optional fields mirror `ntx ledger outcome`; unknown fields are rejected.
- Every experiment ID is checked before any insert; unknown IDs abort the batch.
- A duplicate `(experiment_id, source_id)` within one file is an error.
- Re-importing the same `source_id` with identical fields is a **skip**, so a
  repeated job is idempotent.
- Re-importing the same `source_id` with different fields is an error. A
  correction needs a new `source_id`; the ledger does not overwrite history.
- All inserts use one transaction. A failure rolls back the whole batch.
- `--dry-run` performs full validation and reports `would_import`/`skipped`
  without writing.
- Imported `qc=fail`, `none`, `mixed` and `unclear` records are appended and then
  counted as excluded by scoring, exactly like manually entered outcomes.
- A manual `ntx ledger outcome --source-id ...` can carry the same identity
  semantics.

Import is not automatic extraction: the user supplies structured results, and
the ledger does not verify the source, DOI, registry record or statistical
analysis.

## 4. Export and trust boundary

Ledger export schema is now `ntx-ledger-v3`. It includes experiment `power`
objects and outcome `source_id` values in the SHA-256 content hash. As before,
only `exported_at` is excluded, so two exports of the same ledger share a hash.

`source_id` is a claim by the importer. The system cannot prove the external
result exists, that the source issued it, or that the registration preceded the
experiment. Procedural controls still matter: record a preregistration id, freeze
the database before unblinding, and export the hash before looking at results.
