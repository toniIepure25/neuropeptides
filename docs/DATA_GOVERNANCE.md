# Data governance

## Data classes

| Class | Examples | Where it lives | Public tree |
| --- | --- | --- | --- |
| Bibliographic metadata | PMID, DOI, title, journal, year, publication types | SQLite, sampling frame | yes (titles and identifiers only) |
| CC BY demo abstracts | Europe PMC `abstractText` for 18 explicitly CC BY records | `src/ntx/data/demo_corpus.json` | yes, with attribution |
| Other third-party abstracts | Europe PMC `abstractText` | SQLite, `data/gold/corpus_v2.json`, HTTP cache | **no** |
| Third-party full text | PMC open-access JATS body | SQLite `document_texts`, HTTP cache | **no** |
| Derived annotations | spans, entity IDs, direction/outcome labels, SHA-256 hashes | `gold/`, SQLite | labels and hashes only |
| Builder snippets | `pilot_annotations.json` | git-ignored `data/private_gold/` | **no** |
| Generated artefacts | reports, manifests, build outputs | `reports/`, `build/` | **no** |
| Personal data | none by design | — | — |

No patient-level, biographical or otherwise personally identifying data is
collected. Author names may appear in bibliographic metadata, as in any citation.

## Redistribution policy

- The repository does **not** redistribute arbitrary abstracts or full text.
- The bundled demo corpus contains only records whose Europe PMC metadata reports
  the **CC BY** licence. Every record is attributed in
  `src/ntx/data/ATTRIBUTION.md`; the MIT licence for ntx code does not replace
  the upstream licence for that text.
- Public gold files are **text-free**: sentences and surfaces are replaced by
  SHA-256 hashes. Full text is restored locally with `ntx gold hydrate` against a
  corpus the user is entitled to use.
- `data/private_gold/pilot_annotations.json` contains verbatim snippets and is
  local-only.
- Gold v2 abstracts are fetched into the git-ignored `data/` directory. The
  committed sampling frame records identifiers, retrieval queries, timestamps and
  content hashes, which is enough to re-fetch and verify the exact text.
- Optional full text is fetched from PMC open-access JATS into the local SQLite
  database and cache only. It is never committed, packaged or redistributed; the
  `document_texts.license` field records the upstream licence when supplied.
- Users are responsible for complying with the upstream licence of each record.

## Retention and deletion

- Cached responses and databases are local state; delete `data/`, `build/` and
  the SQLite file to remove all third-party text.
- Re-running `ntx demo` or `scripts/build_gold_v2.py` reconstructs the cache from
  the recorded URLs, subject to upstream availability.
- Redacted annotations remain valid without the corpus; hydration fails closed if
  the corpus no longer matches the recorded SHA-256 hashes.

## Provenance guarantees

1. Every stored abstract claim span is verified against the stored text before a
   report is rendered; every full-text claim is checked against its immutable
   `document_texts` snapshot and `text_hash` (`ntx check`, exit code 1 on either
   mismatch).
2. Hydration verifies every sentence/surface SHA-256 and every span boundary.
3. Every run writes a manifest with code version, git SHA and dirty state, Python
   and platform, ontology/query/corpus hashes, schema version and a
   content-addressed `run_id`.
4. Gold records carry `schema_version`, `annotator` and `status`; model drafts are
   `status: draft` and must never be reported as human gold.

## Publication checklist

- [ ] Gold file hydrated and validated with `ntx gold validate --corpus ...`
- [ ] Content hash in the sampling frame matches the evaluated corpus
- [ ] Kappa and raw agreement reported per field, with n
- [ ] Confidence intervals reported for every P/R/F1
- [ ] Draft/silver annotations labelled as such in the manuscript
- [ ] No unauthorised abstract or full text included in the repository or supplement
- [ ] Licence of every source record checked before redistributing any excerpt
