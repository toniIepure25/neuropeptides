# Limitations and non-goals

## Scientific

- No human-validated gold set exists yet. Draft labels are not accuracy evidence.
- Extraction is a deterministic gazetteer and rule system; it will miss
  paraphrases, implicit entities and claims outside the ontology.
- The bundled demo corpus is small and selected for redistributable licensing,
  not for representativeness.
- Effect extraction is conservative and heuristic. Effect sizes are not pooled
  across incompatible designs.
- Synthesis intervals are descriptive. They are not causal estimates and do not
  replace a systematic review.
- Retraction and metadata checks depend on upstream services; a missing notice is
  not proof that a record is valid.

## Operational

- The local API is a read-only inspection tool, not a public service. It has no
  multi-user authentication, TLS termination or rate limiting.
- SQLite is the only supported store. There is no distributed or concurrent
  writer model.
- Full-text support is limited to cached JATS XML. There is no PDF or OCR path.
- The project does not call an LLM in the evidence path and does not provide a
  chat interface.

## Data and licensing

- Only explicitly CC BY records are bundled. Users are responsible for the
  licences and terms of any corpus they hydrate locally.
- Public gold files are text-free; they are not a redistribution of the source
  abstracts.
- There is no DOI for this software release yet.

## Clinical and regulatory

`ntx` is research software. It is not a medical device, does not provide medical
advice, and must not be used as the sole basis for a clinical decision.
