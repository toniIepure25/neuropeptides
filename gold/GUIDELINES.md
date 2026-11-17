# Gold annotation guidelines (v1)

Scope: title + abstract only. Annotate exactly what the text supports; do not
import knowledge from the full paper.

## Entity mentions

Annotate a mention when the surface text names one of the ontology entities in
`src/ntx/data/ontology.json` (or a clear synonym of one).

- Do **not** annotate generic tokens: "peptide", "neuropeptide", "growth factor",
  "neurotrophic", "neurotropic".
- Do **not** annotate gene-only usage when the sentence is about transcription
  (e.g., "NPY mRNA"), unless the paper also discusses the peptide; record the
  mention form instead.
- Acronyms: annotate only if expanded in the same abstract (`oxytocin (OT)`).
- Record the exact character offsets into `title + "\n\n" + abstract`.

## Claims

Each claim is one abstract sentence containing an entity and an asserted
direction.

| field | values |
| --- | --- |
| direction | increase, decrease, none, mixed, unclear |
| outcome | controlled list in `src/ntx/extract.py` (OUTCOMES) or `other` |
| design | meta_analysis, systematic_review, rct, trial, cohort, case_control, cross_sectional, case_report, animal, in_vitro, review, unknown |

Rules:
1. "No significant effect" / "did not change" -> `none`, even if an effect word
   appears in the sentence.
2. A sentence that reports both an increase and a decrease -> `mixed`.
3. Hedged statements ("may improve") are still claims; mark the hedge in the
   review sheet, not by changing the direction.
4. Do not annotate results from cited references; annotate only the paper's own
   findings.

## Adjudication

Two annotators label the same 25% sample; compute Cohen's kappa on the
(direction, outcome) pair. Disagreements are resolved by a third pass; the
adjudicated label is committed. Target kappa >= 0.70.

## File format (`gold/seed.jsonl`, one JSON object per line)

```json
{"doc_id": "PMID:123", "mentions": [{"start": 10, "end": 18, "entity_id": "NP:OXT", "surface": "oxytocin"}], "claims": [{"entity_id": "NP:OXT", "direction": "increase", "outcome": "social_cognition"}]}
```

Offsets are 0-based, end-exclusive, into `title + "\n\n" + abstract`.
