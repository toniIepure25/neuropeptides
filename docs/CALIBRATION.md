# Calibration of directional predictions

This report describes how well registered probabilities matched observed
agreement. It is **descriptive only**. The ledger is not a random sample, a
registration is not proof that it preceded the experiment, and a small or
selectively reported ledger cannot establish calibration.

## 1. What is being calibrated

Each eligible experiment contributes one row:

- `p` — the registered probability of the predicted direction, in `[0.5, 1.0)`;
- `y = 1` — the observed direction agrees with the prediction;
- `y = 0` — the observed direction is opposite.

Only the latest outcome per experiment is used in the primary analysis.
`qc=fail`, `none`, `mixed`, `unclear` and missing outcomes are excluded and
counted, never imputed. This is the same selection as `ntx ledger score`.

`p` is conditional on a direction being named. It is not a probability that the
hypothesis is true, and it is not the rubric's `low`/`moderate`/`high` evidence
strength.

## 2. Clustering

Two experiments for the same hypothesis are not independent observations. The
default analysis therefore clusters rows by `hypothesis_id` and resamples whole
hypotheses in the bootstrap. `--cluster experiment` treats every experiment as
its own cluster and reproduces the independent-row behaviour for comparison; it
is not the default because it gives intervals that are too narrow when a
hypothesis has repeated experiments.

The report always shows:

- `n` — eligible rows;
- `n_clusters` — distinct clusters;
- `cluster` — `hypothesis` or `experiment`;
- `underpowered_reasons` — `rows<10`, `clusters<10`, or both.

Bootstrap intervals are reported only when `n >= 10` **and** `n_clusters >= 10`.
A single hypothesis with 100 outcomes has one cluster, not 100 independent
observations. Cluster resampling assumes clusters are exchangeable; that is
weaker than assuming rows are exchangeable, but still not a substitute for a
random sample of studies.

## 3. Command

```powershell
ntx ledger calibration --db build/demo.db
ntx ledger calibration --db build/demo.db --cluster experiment
ntx ledger calibration --db build/demo.db --bins 5 --bootstrap 500 --json
ntx ledger calibration --db build/demo.db --out reports/calibration.json
ntx ledger calibration --db build/demo.db --html reports/calibration.html
```

`--bins` is 2–10 (default 5). `--bootstrap` is 0–5000 (default 500; `0` disables
the interval). `--cluster` is `hypothesis` (default) or `experiment`. `--out`
writes canonical JSON; `--html` writes a self-contained report. Both paths create
parent directories. Invalid arguments exit 2; write errors are reported without a
traceback.

## 4. Reliability table

Probabilities are split into equal-width bins between `0.5` and `1.0`:

```text
0.50-0.60
0.60-0.70
0.70-0.80
0.80-0.90
0.90-1.00
```

For each bin with `n_k` rows, the report gives the mean predicted probability
`p̄_k`, the observed agreement rate `ō_k`, the absolute gap `|p̄_k-ō_k|`, the
bin Brier score and the number of clusters. Empty bins are shown, not dropped. A
bin with fewer than 3 rows is marked `*` and listed as sparse.

Sparse bins are expected in small ledgers. They are a warning against reading
individual rows as a reliability curve, not an error.

## 5. Aggregate metrics

With `n` eligible rows:

```text
Brier        = mean((p - y)^2)
Brier skill  = 1 - Brier / 0.25
accuracy     = mean(y)
ECE          = sum_k (n_k / n) * |p̄_k - ō_k|
MCE          = max_k |p̄_k - ō_k|
```

`0.25` is the Brier score of always forecasting `p = 0.5`, so skill is relative to
an uninformative directional forecaster. Accuracy is shown with a 95% Wilson
interval.

The binned Murphy decomposition is also reported:

```text
reliability = sum_k (n_k / n) * (p̄_k - ō_k)^2
resolution  = sum_k (n_k / n) * (ō_k - ō)^2
uncertainty = ō * (1 - ō)
Brier       = reliability - resolution + uncertainty
```

This identity holds for the equal-width bins in the report. It does **not** make
the bins clinically meaningful or the probabilities calibrated.

## 6. Cluster bootstrap

When `n >= 10` and `n_clusters >= 10`, whole clusters are resampled with
replacement 500 times using a fixed seed. The 95% percentile intervals are for
Brier, Brier skill and ECE with the **same bin edges** in every resample. A
cluster selected twice contributes its rows twice.

This is a cluster bootstrap, not a hierarchical model. It does not estimate
between-study variance, adjust for cluster size, or propagate outcome
uncertainty. It is optimistic if clusters are not exchangeable (for example,
if studies were selected because they were easy to replicate).

## 7. Subgroups

`by_kind` and `by_design` summarize eligible rows by hypothesis kind and study
design. A subgroup shows Brier, ECE and accuracy only when it has at least 3 rows
and at least 2 clusters; otherwise the metrics are `null`. Subgroups are
descriptive and are not tested for differences. Pooling across heterogeneous
designs can hide a different calibration pattern, but a small subgroup is not a
finding.

## 8. Outcome precision and corrections

The report classifies each eligible outcome from `effect_size`, `ci_low` and
`ci_high`:

- `ci_consistent` — `increase` with `ci_low > 0`, or `decrease` with `ci_high < 0`;
- `ci_imprecise` — the interval includes zero;
- `ci_inconsistent` — the interval excludes zero in the opposite direction;
- `ci_missing` — one or both bounds are absent.

Counts are shown and inconsistent intervals add a warning. They are data-quality
signals, not grounds for deleting an outcome.

`corrections` counts outcome records and experiments with more than one outcome.
The primary analysis uses the latest outcome; the `first_outcome` sensitivity
shows what happens when the earliest outcome is used instead.

## 9. Sensitivity analyses

Each variant changes one analysis choice at a time:

| Variant | Change |
| --- | --- |
| `first_outcome` | earliest outcome instead of latest |
| `qc_pass_only` | exclude `qc=warn` |
| `precise_ci_only` | only `ci_consistent` outcomes |

Each row reports `n`, `n_clusters`, Brier, ECE, `delta_brier` and `delta_ece`
versus the primary analysis. There are no p-values and no selection of a
"best" variant. The set is fixed before looking at the result, so it is an
audit of fragility rather than a search for a favourable specification.

## 10. Missingness

`missingness` is reported by design: `registered`, `eligible`, `pending`,
`excluded` and `eligible_fraction`. This exposes how much of the registry
contributes to the report. A high eligible fraction is not evidence of
unbiased selection; a low one is a reason not to generalize.

## 11. JSON and HTML artefacts

`--out` writes canonical JSON. `--html` writes one self-contained file with
inline CSS and an inline SVG reliability plot; there are no scripts or external
assets. Dynamic labels are HTML-escaped.

`schema` is `ntx-calibration-v2`. Important fields include `generated_at`,
`content_hash`, `n`, `n_clusters`, `cluster`, `table`, `brier`,
`brier_skill_ci`, `ece`, `mce`, `decomposition`, `bootstrap`,
`underpowered_reasons`, `precision`, `corrections`, `missingness`,
`sensitivity`, `by_kind` and `by_design`.

`content_hash` is SHA-256 over canonical JSON with sorted keys, excluding
`generated_at` and `content_hash`. Two reports over the same data therefore share
the hash even when generated at different times. The hash is an integrity aid,
not a signature or a trusted timestamp.

## 12. Reading the output

Safe statements:

- "The descriptive Brier score on these eligible rows was X."
- "The cluster bootstrap interval was Y, over Z hypotheses."
- "The reliability table is sparse and the ledger is underpowered."
- "The first-outcome sensitivity changed Brier by Δ."

Unsafe statements:

- "The model is calibrated."
- "A probability of 0.8 means an 80% chance of replication."
- "The rubric is validated."
- "Subgroup A is better calibrated than subgroup B."
- Any ranking of candidates or claims from this report.

The report always says `descriptive_only: true`. Below 10 rows or 10 clusters it
adds `underpowered: true` and the reasons. Even above those thresholds, selection
and publication bias remain.

## 13. What this report does not do

- It does not verify that a registration preceded an experiment.
- It does not verify imported `source_id` values or external results.
- It does not estimate a rubric probability, a replication probability or a
  treatment effect.
- It does not implement a hierarchical/Bayesian model, calibration slope or
  intercept, cluster-robust regression or multiple-comparison correction.
- It does not turn a small, clustered or selectively reported ledger into
  calibration evidence.
