# Gold annotation protocol v2

Status: **infrastructure complete, human annotation pending.** The files in
`gold/v2/` are a frozen sampling frame, a model **draft** pre-annotation, and an
annotation tool. No human gold exists yet, so no gold-set metric may be reported
from this directory until the workflow below has been completed and adjudicated.

## 1. What is being measured

| Layer | Unit | Match rule |
| --- | --- | --- |
| Detection | entity mention | exact character span in `title + "\n\n" + abstract` |
| Normalization | mention + ontology ID | same span and same ID |
| Claim extraction | claim tuple | `(doc_id, entity_id, direction, outcome, sentence start)` |
| Direction | claim direction | relaxed match on `(doc_id, entity_id, direction)` |
| Integrity | provenance | every stored span reproduces its quoted text exactly |

Mentions are only those that resolve to an ontology entity (`NP:*`, `NTF:*`,
`NTS:*`). Generic words ("neuropeptide", "growth factor") are not mentions.

## 2. Sampling frame (frozen)

Built by `scripts/build_gold_v2.py` with `NTX_FROZEN_TIME` set, from Europe PMC
`resultType=core`:

| Compound | Human trial | Observational | Preclinical |
| --- | --- | --- | --- |
| Oxytocin (OXT) | 8 | 8 | 8 |
| Neuropeptide Y (NPY) | 8 | 8 | 8 |
| Substance P (SP) | 8 | 8 | 8 |
| BDNF | 7 | 8 | 8 |
| Semax / Selank | 8 | 8 | 8 |

- **119 documents** after cross-stratum deduplication (the retrieval pool is
  finite; one record appeared under two cells and was kept once).
- Strata are query-defined proxies: `human_trial` = RCT/meta-analysis/systematic
  review publication types; `observational` = cohort/cross-sectional/observational
  with RCTs and meta-analyses excluded; `preclinical` = mice/rats/in-vitro/cell
  culture with human trial types excluded.
- The exact queries, hit counts, retrieval timestamp and per-document content
  hashes are in `gold/v2/sampling_frame.json`. Do not edit that file; rebuild it
  if the frame must change.
- Full abstracts live in `data/gold/corpus_v2.json` (git-ignored). The repository
  stores identifiers, bibliographic metadata and annotation spans only.

## 3. Record schema (v2)

One JSON object per line in `gold/v2/*.jsonl`:

```json
{
  "schema_version": 2,
  "doc_id": "PMID:42639841",
  "annotator": "AB",
  "status": "human",
  "mentions": [
    {"entity_id": "NP:OXT", "start": 10, "end": 18, "surface": "oxytocin", "mention_form": "peptide"}
  ],
  "claims": [
    {
      "entity_id": "NP:OXT",
      "start": 0,
      "end": 96,
      "sentence": "Intranasal oxytocin reduced ...",
      "direction": "decrease",
      "outcome": "anxiety",
      "role": "intervention",
      "design": "rct",
      "section": "results",
      "evidence_start": 0,
      "evidence_end": 33
    }
  ]
}
```

`section`, `evidence_start` and `evidence_end` are optional and recorded by the
model draft / annotator tool; when present, the evidence span must lie inside the
claim sentence.

Enumerations (validated by `ntx gold validate`):

- `direction`: `increase`, `decrease`, `none`, `mixed`, `unclear`
- `outcome`: `cognition`, `memory`, `attention`, `social_cognition`, `anxiety`,
  `depression`, `feeding`, `pain`, `neuroplasticity`, `neuroprotection`, `other`
- `role`: `intervention`, `comparator`, `mechanism`, `unknown`
- `design`: `meta_analysis`, `systematic_review`, `rct`, `trial`, `cohort`,
  `case_control`, `cross_sectional`, `animal`, `in_vitro`, `review`, `unknown`
- `mention_form`: `peptide`, `gene`, `acronym`, `brand`, `other`

Rules that the validator enforces when a corpus is supplied:

1. `text[start:end] == surface` for mentions and `== sentence` for claims.
2. No duplicate mention `(entity_id, start, end)` or claim
   `(entity_id, start, end, direction, outcome)` per document.
3. Enumerated fields must be in range; `0 <= start < end`; span inside the text.

## 4. Annotation workflow

Two independent annotators; adjudication by a third pass or by consensus.

1. `python scripts/build_gold_v2.py` — already run; reproduces the frame
   (network required, writes `data/gold/corpus_v2.json`).
2. Serve the repository locally and open the tool:
   ```powershell
   python -m http.server 8000
   # http://localhost:8000/gold/v2/annotator.html
   ```
3. Annotator A reviews **all 119 documents**. The bundled draft is only a recall
   aid: accept, edit (direction/outcome/role/design) or delete every draft claim,
   and add missing claims or mentions by selecting the span in the text. The
   draft is regenerated from extraction v2 (`model-draft-v0.3`) and carries role,
   section and evidence span; it is **not** gold.
   Use the **Validate** button before exporting; it checks span/sentence
   agreement, enumerated fields and evidence containment.
4. Export `annotations_<initials>.jsonl`. Store it as
   `gold/v2/annotations/<initials>.jsonl` (not committed until adjudicated).
5. Annotator B independently annotates the **first 30 documents** in the same
   format, without seeing A's file.
6. Validate, measure agreement, adjudicate:
   ```powershell
   ntx gold validate --file gold/v2/annotations/AB.jsonl --corpus data/gold/corpus_v2.json
   ntx gold validate --file gold/v2/annotations/CD.jsonl --corpus data/gold/corpus_v2.json
   ntx gold agreement --a gold/v2/annotations/AB.jsonl --b gold/v2/annotations/CD.jsonl --field direction
   ntx gold agreement --a gold/v2/annotations/AB.jsonl --b gold/v2/annotations/CD.jsonl --field outcome
   ```
7. Generate the adjudication skeleton for every field-level disagreement:
   ```powershell
   ntx gold conflicts --a gold/v2/annotations/AB.jsonl `
                      --b gold/v2/annotations/CD.jsonl `
                      --fields direction,outcome,role,design `
                      --out gold/v2/adjudications.jsonl
   ```
   Fill the `value` column for each row after discussion. Rows left blank are
   ignored by `gold merge` (they are unfinished, not decisions). Then write the
   merged gold set:
   ```powershell
   ntx gold merge --primary gold/v2/annotations/AB.jsonl `
                  --secondary gold/v2/annotations/CD.jsonl `
                  --adjudications gold/v2/adjudications.jsonl `
                  --out gold/v2/gold.jsonl
   ```
8. `ntx gold validate --file gold/v2/gold.jsonl --corpus data/gold/corpus_v2.json`
   must report zero errors before metrics are computed.

## 5. Agreement and quality gates

- Report Cohen's kappa per field on the 30 double-annotated documents, with the
  raw agreement and n. Kappa is computed over claim keys
  `(doc_id, entity_id, sentence start)` shared by both annotators; claims split
  within one sentence are represented as a joined label (`decrease+none`), so
  splitting disagreements are visible rather than dropped.
- Gates for using the gold set as a benchmark: direction kappa >= 0.70,
  outcome kappa >= 0.60, role kappa >= 0.60, **and** 100% span validity.
  A kappa below 0.40 for any field means the guideline or schema is revised
  before metrics are reported; the revision is logged in `CHANGELOG.md`.
- Report bootstrap 95% confidence intervals (document-level resampling) for every
  P/R/F1, never a bare point estimate.

## 6. Known ambiguities to adjudicate explicitly

1. Multi-finding sentences: one claim row per clause/finding; never merge
   different directions into one row.
2. Background statements ("has been shown to ...") are not this paper's finding
   unless the paper reports the result itself.
3. A compound used as a comparator (`oxytocin alone`) gets `role: comparator`, not
   `intervention`.
4. A sentence with no explicit entity is not annotated; coreference is out of
   scope for v2 (documented as a recall limitation, not an annotation target).
5. Outcome labels follow the measured endpoint, not the authors' interpretation:
   "improved social preference in a mouse model" is `social_cognition`, and a
   molecular readout is `neuroplasticity`/`other` even when the discussion claims
   cognitive benefit.

## 7. Licensing and data protection

Abstracts are third-party text. They are fetched into the ignored `data/`
directory for annotation and evaluation and are not redistributed. Committed
files contain bibliographic metadata (title, journal, year, identifiers) and
span offsets into the local corpus; the corpus hash makes the exact text
verifiable. No patient-level or personally identifying data is involved.

## 8. Current artefacts

| File | Purpose | Committed |
| --- | --- | --- |
| `gold/v2/sampling_frame.json` | frozen frame, queries, hashes | yes |
| `gold/v2/draft.jsonl` | model pre-annotation (NOT gold) | yes |
| `gold/v2/annotator.html` | offline span/label annotation tool | yes |
| `data/gold/corpus_v2.json` | abstracts for annotation | no (git-ignored) |
| `gold/v2/annotations/*.jsonl` | per-annotator exports | after adjudication |
| `gold/v2/gold.jsonl` | adjudicated gold | when complete |
