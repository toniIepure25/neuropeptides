# ADR 0002 — No LLM in the core extraction path

- Status: accepted (2026-11-13)
- Deciders: maintainers

## Context

An LLM extractor would likely raise recall on hedged, implicit or
multi-clause claims. It would also add a non-deterministic dependency, network
calls, per-token cost, model drift and an unauditable failure mode to a system
whose main claim is provenance.

## Decision

The default pipeline is **rules only**: gazetteer matching, clause segmentation,
cue-based direction, regex design/sample-size detection and a deterministic
rubric. No model is downloaded or called at runtime. Every claim stores the
sentence and evidence span that produced it, plus a list of reasons.

An LLM extractor may be added later as an **opt-in adapter** that emits the same
claim schema, is scored by the same gold and ablation harness, and never becomes
the only path.

## Consequences

- Runs are reproducible byte-for-byte with `--frozen-time`.
- Offline operation and an installable wheel with no model assets.
- Lower ceiling on implicit claims; this is measured, not hidden.
- Any future adapter must pass provenance checks and the paired ablation gate
  before it can be recommended.

## Alternatives rejected

- **LLM-first with rules as fallback.** Cost, drift and auditability make the
  default path untestable.
- **LLM only for normalization.** Still needs network access and a pinned model;
  gazetteer + UniProt/ChEBI IDs are sufficient for the current entity set.
