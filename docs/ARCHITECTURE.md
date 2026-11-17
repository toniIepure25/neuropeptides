# Architecture

## Data flow

```
Europe PMC / PubMed / Crossref          bundled corpus (offline demo)
            │                                     │
            └──────────► ingest ◄─────────────────┘
                            │
                   documents (SQLite)
                            │
                     nlp.Lexicon (gazetteer + annotations)
                            │
                   nlp / extract (mentions, claims)
                            │
                     appraise (design, bias, rubric)
                            │
                     report (Markdown/HTML)   discovery (hypotheses)
                                │                        │
                     fulltext (PMC JATS) ──► fulltext claims
                                │                        │
                                └──────► reconcile (claim-level report)
                                                        │
                                             precursors ──► generate
                                             (protein)      (cleavage / analogs)
                                                                │
                                                     candidates ──► ledger
                                                     (sequence,     (prediction before
                                                      descriptors)   outcome, Brier score)
```

## Modules

| Module | Responsibility |
| --- | --- |
| `ntx.config` | Bundled ontology/queries, path resolution, `NTX_*` overrides |
| `ntx.build_paths` | CLI defaults for database and corpus |
| `ntx.http` | Cached JSON/text transport, per-host throttling, offline guard |
| `ntx.ingest` | Europe PMC/PubMed/Crossref adapters; document identity |
| `ntx.verify` | Read-only Crossref/PubMed metadata verification and drift report |
| `ntx.fulltext` | Cached PMC JATS fetch, section parsing, full-text claims and report wrappers |
| `ntx.reconcile` | Claim-level abstract/full-text statuses, reasons, severity and content hash |
| `ntx.nlp` | Longest-match gazetteer, acronym disambiguation, annotation import |
| `ntx.extract` | Clause-aware direction/negation/hedge, role, section, evidence span |
| `ntx.quant` | Conservative effect type/size/CI and comparator/dose/route/timepoint extraction |
| `ntx.quality` | Schema, provenance, effect and gold quality gates with a content hash |
| `ntx.appraise` | PubType design mapping, retraction status, rubric, consistency |
| `ntx.store` | SQLite persistence, schema versioning/migrations, provenance check |
| `ntx.pipeline` | End-to-end processing, demo runner, run records |
| `ntx.report` | Evidence briefs; refuses to render on provenance failure |
| `ntx.synthesis` | Design-weighted per-entity/outcome synthesis with bootstrap CI |
| `ntx.corpus` | Corpus JSON loader for evaluation and ablation |
| `ntx.ablate` | Paired extraction-flag ablations with delta-F1 intervals |
| `ntx.eval` | Gold loading, precision/recall/F1, error diffing |
| `ntx.annotation` | Gold schema v2 validation, agreement keys, adjudication merge |
| `ntx.stats` | Cohen's kappa, document-level bootstrap confidence intervals |
| `ntx.discovery` | Ranked gap hypotheses with assay/design/risk |
| `ntx.candidates` | Candidate validation, content ids and heuristic descriptors |
| `ntx.precursors` | User-supplied precursor protein records |
| `ntx.generate` | Named cleavage rules, analog enumeration, append-only lineage |
| `ntx.power` | Two-arm power worksheets and recomputation validation |
| `ntx.calibration` | Cluster-aware reliability bins, sensitivities, content hash |
| `ntx.calibration_html` | Self-contained HTML/SVG rendering for calibration |
| `ntx.ledger` | Append-only predictions/outcomes, Brier scoring, export and checkpoints |
| `ntx.dashboard` | Self-contained HTML quality and reconciliation view |
| `ntx.dbadmin` | Backup sidecars, verification, restore and operational doctor |
| `ntx.serve` | Read-only local JSON API over a built database |
| `ntx.settings` | Optional `ntx.toml` with typed validation and precedence |
| `ntx.manifest` | Content-addressed run manifests |
| `ntx.clock` | Frozen-time mode for deterministic runs |
| `ntx.log` | JSON logging to stderr |

## Invariants

1. **Provenance.** Every claim stores `doc_id`, `start`, `end` and `sentence`, and
   `text[start:end] == sentence` must hold. Abstract claims check against title +
   abstract; full-text claims check against the exact `document_texts` snapshot and
   its `text_hash`. Quantitative fields additionally store exact quotes and spans
   for effect values, CIs and context, and validation rejects any mismatch.
   `report.render_all` refuses to write abstract reports when the
   abstract check fails; `ntx check` checks both layers and exits non-zero on
   either.
2. **No silent normalization.** Mentions must resolve to an ontology ID; unmatched
   surfaces are dropped, not guessed.
3. **Determinism.** With `--frozen-time`, identical inputs and code produce
   byte-identical reports and a stable manifest `run_id`.
4. **Self-contained wheel.** Ontology, queries and the demo corpus ship inside the
   package; the installed CLI never depends on the repository layout.
5. **Append-only ledger.** Candidate, precursor, lineage, experiment, prediction
   and outcome rows cannot be updated or deleted through the supported API; SQLite
   triggers reject those operations. Corrections are new outcome rows, and an
   external outcome `source_id` is unique within an experiment.
6. **Recomputed design sheets.** A stored power worksheet is accepted only when
   its declared inputs reproduce the same n values and deterministic `power_id`.
7. **Descriptive calibration.** The calibration report reuses eligible ledger rows,
   clusters by hypothesis by default, keeps empty/sparse bins visible, and marks
   itself `descriptive_only`; no calibration claim is made. Its content hash
   excludes the generation timestamp.
8. **Integrity is layered.** `ntx verify` reads external metadata without mutating
   the database; a missing notice is not proof of validity. A ledger checkpoint
   chains the export content hash, but it is neither a trusted timestamp nor a
   signature. Full-text snapshots are immutable through the supported API and are
   verified separately; they do not alter abstract reports, discovery or exports.

## Schema versioning

`Store` applies `PRAGMA user_version` migrations at open time (`MIGRATIONS` in
`ntx/store.py`). The current version is 8, which adds conservative quantitative
columns (`effect_type`, `effect_size`, `effect_ci_low`, `effect_ci_high`, `quant`)
to abstract and full-text claims. Migration is additive, idempotent and preserves
existing abstract data; rows migrated from v7 keep `quant = '{}'`.

## Architecture decisions

- [ADR 0001](adr/0001-claim-granularity.md) — claims are clause-scoped
  entity–outcome assertions.
- [ADR 0002](adr/0002-no-llm-core.md) — no LLM in the core extraction path.
- [ADR 0003](adr/0003-synthesis-weights.md) — design-weighted synthesis with
  document-level bootstrap intervals.

## Reproducibility artefacts

`ntx demo` writes `build/manifests/<run_id>.json` containing the git SHA and dirty
state, code version, Python/platform, hashes of the ontology, query set and
corpus, and the schema version. The `run_id` is a content hash that excludes the
creation timestamp, so reruns share an identity even at different times.

## Failure modes (accepted, documented)

- Abstract-only extraction is still the default: randomization/blinding in full
  Methods is read only when the opt-in full-text layer has a PMC JATS body.
- Full-text coverage is limited to open-access PMC XML; publisher PDFs, figures and
  table cells are not parsed.
- External metadata lags publication and errs in both directions; `ntx verify`
  reports a snapshot, not a guarantee that a paper is valid.
- A checkpoint chain detects changes after an anchored snapshot, not an attacker
  who rewrites the entire chain before it is anchored.
- Direction is read per clause, but nested negation and implicit comparisons are
  still partially handled (see `docs/DISCOVERY.md` and the evaluation notes).
- Europe PMC annotations are noisy; they supplement, never replace, the gazetteer.
