# Open-access full text

This document defines the v0.14 full-text evidence layer. It is optional and
separate from the abstract pipeline: existing reports, discovery, calibration and
ledger exports keep using abstract claims unless a caller explicitly asks for the
full-text reconciliation or the `--with-fulltext` brief section.

## 1. Scope

The layer covers papers indexed in Europe PMC with a PMC identifier and an
open-access JATS XML body:

```text
https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/fullTextXML
```

It does not read publisher PDFs, supplementary files, figures or table cells
comprehensively. Papers without a PMCID or without an available body are reported
with a status and skipped, not treated as invalid.

Full text is local research state. The repository and wheel do not contain it, and
`ntx` does not redistribute it.

## 2. Commands

```powershell
ntx fulltext fetch --db build/demo.db --limit 10
ntx fulltext fetch --db build/demo.db --doc-id PMID:12345678 --offline --json
ntx fulltext extract --db build/demo.db --limit 10
ntx fulltext report --db build/demo.db --json --out reports/fulltext.json
ntx fulltext report --db build/demo.db --entity NP:OXT --fail-on high
ntx fulltext report --db build/demo.db --fail-on-conflict
ntx report --db build/demo.db --out reports/fulltext-aware --with-fulltext
```

`fetch` stores one immutable snapshot per document; a second run reports
`already_present` rather than overwriting it. `extract` is idempotent through a
unique `(doc_id, entity_id, start, end)` constraint. `report` is read-only.

## 3. Fetch statuses

| Status | Meaning |
| --- | --- |
| `fetched` | Parsed and stored |
| `already_present` | A snapshot exists; not overwritten |
| `no_pmcid` | Document has no PMC identifier |
| `invalid_pmcid` | Identifier is not of the form `PMC<digits>` |
| `unavailable` | Offline cache miss, network failure or non-size HTTP failure |
| `too_large` | XML or normalized text exceeds the cap |
| `unsafe_xml` | `DOCTYPE` or `ENTITY` declaration |
| `invalid_xml` | Not well-formed XML |
| `empty` | No JATS body paragraphs |

A completed fetch command exits `0` even when individual papers are unavailable;
the status table is the result. Argument or output-write failures exit `2`.

## 4. Parser

`parse_jats` uses `xml.etree.ElementTree` and only reads the article `body`. The
abstract is already stored in `documents` and is not duplicated.

Rules:

- reject `DOCTYPE`/`ENTITY` before Expat sees the document;
- normalize inline tags and whitespace to plain text;
- join paragraphs with `\n\n`;
- record a section start offset for each contiguous section;
- map known titles to `methods`, `results`, `discussion`, `conclusions`,
  `introduction`, `background`; unknown subsections inherit the nearest known
  parent and top-level unknowns become `other`;
- keep `caption` text for tables and figures, but not table cells;
- cap XML at 8,000,000 characters and normalized text at 2,000,000 characters.

## 5. Extraction

The extractor detects mentions over the normalized full text and passes the JATS
section offsets to `extract_claims(..., section_labels=...)`. Only claims whose
section is `results`, `discussion` or `conclusions` are stored. This avoids the
abstract's Background/Methods ambiguity for open-access papers.

Each claim stores:

- `doc_id`, `entity_id`, direction, outcome, species, design, n, p-value;
- exact `start`/`end` into `document_texts.text`;
- `sentence`, evidence span, role, section, resolver and confidence reasons;
- `text_hash`, equal to the snapshot's SHA-256 content hash.

The snapshot text is never updated by the supported API, so a claim cannot silently
point at a different revision.

## 6. Schema v8

Schema v8 keeps the v7 snapshot/claim tables and adds `effect_type`,
`effect_size`, `effect_ci_low`, `effect_ci_high` and `quant` JSON to both claim
tables. The migration is additive and idempotent.

```sql
document_texts (
  doc_id TEXT PRIMARY KEY,
  pmcid TEXT NOT NULL,
  source_url TEXT NOT NULL,
  text TEXT NOT NULL,
  sections TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  license TEXT DEFAULT ''
)

fulltext_claims (
  claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL,
  ...
  text_hash TEXT NOT NULL,
  effect_type TEXT DEFAULT '',
  effect_size REAL,
  effect_ci_low REAL,
  effect_ci_high REAL,
  quant TEXT DEFAULT '{}',
  UNIQUE(doc_id, entity_id, start, end)
)
```

`ntx check` verifies both abstract and full-text claims. `report.render_all` only
checks abstract provenance by default; with `include_fulltext=True` (the
`--with-fulltext` CLI path) it also checks full-text provenance and refuses to
render on failure. The ledger export remains `ntx-ledger-v3` and contains no
full-text fields.

## 7. Claim-level reconciliation

Schema `ntx-fulltext-report-v4` groups stored claims by
`(doc_id, entity_id, outcome)` and classifies each group:

| Status | Meaning |
| --- | --- |
| `corroborated` | The two layers share at least one direction for the outcome |
| `direction_conflict` | Both sides have comparable directions, but none overlap |
| `abstract_only` | The snapshot has no full-text claim for the group |
| `fulltext_only` | The abstract has no claim for the group |
| `indeterminate` | At least one side has no comparable direction |

The report does not force one-to-one sentence matches. A group can contain several
abstract and full-text claims; the finding lists up to five exact-span references
per side and keeps the full counts separately.

### Reasons and severity

| Reason | Severity | Rule |
| --- | --- | --- |
| `direction_conflict` | high | Status is `direction_conflict` |
| `significance_conflict` | high | Minimum p-value crosses 0.05 between sides |
| `sample_size_discrepancy` | medium | Max n differs by more than 20% and the larger n is at least 10 |
| `hedging_mismatch` | low | The abstract is unhedged and every full-text claim is hedged |
| `discussion_only` | low | Every full-text claim is in Discussion or Conclusions |
| `abstract_sentence_in_fulltext` | low | An abstract-only sentence (at least 40 characters) appears verbatim in the snapshot |
| `mixed_directions` | low | Increase and decrease both occur on one side |
| `effect_size_discrepancy` | medium | Both sides have a comparable effect, CIs, and non-overlapping 95% CIs |
| `effect_type_mismatch` | low | Explicit effect types differ between sides |
| `effect_ambiguous` | low | One side has more than one comparable effect, so no single effect is chosen |
| `comparator_mismatch` | low | Explicit comparator quotes differ |
| `dose_route_mismatch` | low | Explicit dose or route quotes differ |
| `population_mismatch` | low | Explicit population quotes differ |
| `timepoint_mismatch` | low | Explicit timepoint quotes differ |

Each finding includes an `effect_comparison` object with `status` (`absent`,
`one_sided`, `corroborated`, `mismatch` or `ambiguous`; ambiguity is scoped to
the effect comparison, not the top-level finding), both explicit effects and a CI-overlap
flag. Effect-size discrepancy is emitted only for a corroborated effect with both
CIs non-overlapping; context mismatches require explicit quotes and do not fire on
missing context.

`info` is used for a plain corroborated group with no low/medium/high reason. A
group with a low reason is `low`, not `info`. Reasons are descriptive review
triggers; they are not error labels.

### Determinism and filters

`content_hash` covers the canonical payload excluding `generated_at` and itself.
The report verifies abstract and full-text provenance before building. `--entity`
filters to one ontology entity id, `--fail-on` exits `1` when any finding meets or
exceeds `none`, `low`, `medium` or `high`, and `--fail-on-conflict` remains an alias
for `direction_conflict`. The default is `--fail-on none`, so a report can be
inspected without failing a pipeline.

`ntx report --with-fulltext` adds a compact status/severity table and up to ten
findings to each evidence brief. Every entity gets an explicit "no reconcilable
groups" note when none exist. The default `ntx report` output is unchanged, and the
reconciliation section never promotes full-text claims into the brief's appraisal
or synthesis tables.

`corroborated` means only that the stored abstract and full-text layers contain a
matching direction. It does not prove that either sentence is true, that the effect
is correct, or that the abstract faithfully represents the full article.

## 8. Data governance and security

- Store full text only in the local SQLite database and HTTP cache; delete both to
  remove it.
- Do not commit, package or publish the database or cache.
- Check the upstream licence recorded in `document_texts.license` before quoting or
  redistributing any excerpt.
- XML is untrusted input: scheme, size, `DOCTYPE`/`ENTITY` and parser errors are
  handled explicitly. The parser is not a general XML sanitizer.

## 9. Limits

- No PMCID means no full-text layer, even if a publisher page exists.
- JATS coverage differs by journal; some bodies are unavailable or incomplete.
- Figure and table cells are not extracted; captions only.
- Section titles are heuristic beyond the explicit JATS structure.
- Full-text-only claims do not enter the default evidence briefs, discovery,
  calibration or ledger. They are a separate inspectable layer; the optional brief
  section reports reconciliation but does not promote them.
- The report compares stored claims, sentence-level directions and explicit
  heuristic effects; it does not establish semantic equivalence, verify effect
  extraction or cover the complete article.
- `abstract_sentence_in_fulltext` is an exact normalized-whitespace/case phrase
  match, not a plagiarism or fidelity detector.
