# Evaluation protocol and current status

This page separates software verification from scientific validation. Passing
tests do not establish extraction accuracy, and no human-validated gold metrics
are reported for this release.

## Unit of analysis

| Task | Unit | What is compared |
| --- | --- | --- |
| Mention detection | `(doc_id, entity_id, span)` | Exact span and ontology id |
| Claim extraction | `(doc_id, entity_id, direction, outcome, span)` | Claim tuple and exact span |
| Effect extraction | `(doc_id, claim_id, effect fields)` | Type, size, CI, comparator, context |
| Appraisal | `(doc_id, design, species, risk flags)` | Design and bias fields |

A prediction matches only when the entity and outcome agree and the predicted
span is the annotated span. Partial sentence overlap is not a match.

## Gold artefacts

- `gold/pilot.jsonl` — small, text-free pilot with labels and spans.
- `gold/v2/draft.jsonl` — 119-document frame; **model-assisted draft**, not human
  gold.
- `gold/v2/sampling_frame.json` — frame and sampling metadata.

Public files replace sentences and surfaces with SHA-256 hashes. Hydration
against a locally held corpus verifies every hash and span before evaluation.
`ntx eval` refuses redacted input rather than scoring labels without text.

## Gates and reporting

- No precision, recall or F1 is reported as “human” unless two independent
  annotators and adjudication exist.
- Agreement is reported with Cohen's kappa and explicit denominators.
- Effect quality is reported separately from extraction quality.
- Confidence intervals use document-level bootstrap where implemented; they are
  descriptive, not causal.
- Any future human evaluation must publish the annotation protocol, sampling
  frame, adjudication rules and per-task denominators.

## Current status

| Item | Status |
| --- | --- |
| Human double annotation | Not available |
| Adjudicated human gold | Not available |
| Model-assisted draft labels | Available, clearly marked |
| Pipeline stability tests | Available |
| Human accuracy claim | **None** |

The correct description of this release is: provenance and engineering
behaviour are tested; scientific accuracy remains to be established.
