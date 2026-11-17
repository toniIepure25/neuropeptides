# ADR 0003 — Design-weighted synthesis with document-level bootstrap intervals

- Status: accepted (2026-11-13)
- Deciders: maintainers

## Context

The briefs answer "what is reported" per document. A portfolio view needs a
per-entity/per-outcome summary that does not let ten cell-culture abstracts
outvote one randomized trial, and that states its uncertainty without pretending
to be a meta-analysis.

## Decision

For each (entity, outcome):

1. Each document contributes **once**, using its strongest design.
2. Support and refute weights are the design priors in
   `synthesis.DESIGN_WEIGHTS` (`meta_analysis` 1.0 … `in_vitro` 0.25).
3. `pooled_support = support / (support + refute)`; neutral (`none`/`mixed`/
   `unclear`) is reported but not in the denominator.
4. The 95% interval is a **document-level bootstrap** over pooled support.
5. A row is `fragile` when the interval crosses 0.5 or has fewer than three
   documents.
6. Comparator claims are excluded unless `--include-comparator` is passed.
7. Output carries an explicit disclaimer: weights are uncalibrated priors.

## Consequences

- The summary is robust to abstract-count inflation by weak designs.
- The interval reflects between-document variability, not sampling error of a
  pooled effect; it will be wide and that is honest.
- Weights are a policy choice; the ablation/config surface makes them visible
  and testable.
- No claim of clinical efficacy is made or implied.

## Alternatives rejected (for now)

- **Fixed/random-effects meta-analysis.** Requires comparable effect sizes,
  which abstracts rarely report in extractable form.
- **Bayesian hierarchical model.** Better long-term, but needs a calibration
  set and a sampler; deferred until human gold exists.
- **Simple vote counting.** Lets weak designs dominate and hides heterogeneity.
