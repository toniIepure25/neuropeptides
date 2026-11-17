# ADR 0001 — Claims are clause-scoped, entity–outcome assertions

- Status: accepted (2026-11-13)
- Deciders: maintainers
- Supersedes: implicit "one sentence, one claim" rule in model v1

## Context

The v1 extractor attached a direction to every (sentence, entity) pair. That
over-attributes in abstracts that report several findings per sentence
(`"OT improved X but reduced Y"`), and it made comparator findings look like
intervention findings. Any downstream synthesis inherited the error.

## Decision

A claim is one **assertion** with:

- one document, one ontology entity, one outcome;
- one direction (`increase`/`decrease`/`none`/`mixed`/`unclear`);
- one **role** (`intervention`, `comparator`, `mechanism`);
- the **evidence span** that carries the direction cue, which must lie inside the
  claim sentence;
- the enclosing sentence and its absolute `start`/`end`.

Sentences are split on `;`, `(n)`, `but`, `however`, `whereas`, `while` and
similar contrast markers. Direction is read from the clause that contains the
entity, with negation and hedging evaluated locally. Provenance is
`text[start:end] == sentence`; the evidence span must satisfy
`start <= evidence_start <= evidence_end <= end` within the same document.

## Consequences

- One sentence can yield several claims; counts are not sentence counts.
- Report contradictions count only `intervention` claims, so a comparator arm
  cannot create a contradiction by design.
- Extraction is more expensive (clause split + local direction) but remains
  linear in abstract length.
- A gold annotation must carry role and section; agreement keys include them.

## Alternatives rejected

- **Sentence-level claims with a multi-direction field.** Still conflates
  entities and comparators, and loses the evidence quote.
- **Token-level relation extraction with a learned model.** Better ceiling, but
  violates ADR 0002 (no opaque core) and cannot be audited in the current phase.
