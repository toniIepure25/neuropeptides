# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.18.0] - 2026-11-17

Research-grade hardening, a public API contract and an attested release path.

### Added
- `GET /api/v1/openapi.json`: an OpenAPI 3.1 contract for every read-only API
  route, generated from package metadata and checked against the server route
  table in CI.
- `scripts/check_api_contract.py`, `scripts/check_docs_links.py` and
  `scripts/benchmark.py`.
- MkDocs Material documentation site with a strict build, new CLI reference,
  evaluation protocol, limitations page and deployment to GitHub Pages.
- Fixed-seed property tests for redaction/hydration round-trips, hash tampering,
  invalid spans and non-mutating redaction.
- CodeQL, OpenSSF Scorecard, dependency review and Dependabot auto-merge
  workflows.
- CycloneDX SBOM, SHA-256 checksums and GitHub build-provenance attestations in
  the release workflow; attestations are verified before publication.
- `GOVERNANCE.md`, `ROADMAP.md`, `SUPPORT.md`, `MAINTAINERS.md` and
  `.github/CODEOWNERS`.
- Restrictive `Content-Security-Policy` and `Cross-Origin-Resource-Policy`
  headers on API responses.

### Changed
- `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/PERFORMANCE.md`,
  `docs/RELEASE_CHECKLIST.md`, `SECURITY.md` and `README.md` describe the new
  contract, supply-chain and documentation surfaces.
- All GitHub Actions are pinned to immutable commit SHAs. Dependabot groups
  routine updates; existing major toolchain updates were merged only after the
  full CI matrix passed.
- The release checklist now requires strict docs, link and API checks plus SBOM
  and attestation verification.

### Security
- CodeQL Python analysis, dependency review and OpenSSF Scorecard added.
- SBOM and provenance attestations added to releases.
- API response hardening extended with CSP and cross-origin resource policy.

## [0.17.0] - 2026-11-17

Public-release hardening and the first sanitized open-source distribution.

### Added
- `ntx gold redact` and `ntx gold hydrate` for text-free annotation labels with
  exact spans and SHA-256 sentence/surface hashes. Hydration verifies every hash
  and fails closed on a changed or out-of-range corpus.
- `scripts/check_release.py` for version, CITATION, changelog, release-note,
  repository-URL, PEP 561 and demo-attribution consistency.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue and pull-request templates,
  Dependabot configuration, and a tag-driven GitHub release workflow.
- `src/ntx/py.typed` and the `Typing :: Typed` classifier.
- `gold/README.md` and `src/ntx/data/ATTRIBUTION.md`.

### Changed
- Public `gold/pilot.jsonl` and `gold/v2/draft.jsonl` are redacted. Verbatim
  gold sentences are local-only and restored with `ntx gold hydrate`.
- `ntx eval` refuses redacted gold with an actionable usage error.
- The bundled demo corpus is restricted to 18 explicit CC BY records; the
  previous mixed-licence snapshot is no longer redistributed.
- The legacy pilot labels are normalized to schema v2 and one exact duplicate
  claim is removed; no labels or spans were changed.
- README rewritten around installation, architecture, evidence guarantees,
  evaluation status, data policy and reproducibility.
- Repository URLs, author metadata, security policy, release checklist and
  method notes updated for the public repository.

### Removed
- `gold/pilot_annotations.json` from the public tree (builder snippets are
  local-only).
- Stale example URLs and the mixed-licence demo records.

## [0.16.0] - 2026-11-17

Effect rigor and operational safety.

### Added
- Bounded confidence-interval ownership to the nearest effect and explicit
  `effect_ambiguous` handling for multi-effect claims.
- Effect-only audit sampling and a separate human effect verdict.
- Quality payload v2 with thresholds, ambiguous counts and `--require-gold`.
- Copy-verified backups, verified restores and `ntx doctor` improvements.
- API security headers and a minimum token length for non-loopback binds.

### Changed
- Effect MAE is computed within effect type only; cross-type comparisons are
  reported descriptively rather than as a single error.
- Full-text report schema v4 and quality schema v2.

## [0.15.0] - 2026-11-17

Conservative quantitative extraction, schema v8, effect-aware quality and
reconciliation, operational database tools, and a hardened local API.

### Added
- `ntx.quant` for explicit SMD/Cohen's d/Hedges' g, mean difference, OR/RR/HR,
  beta, correlation and signed percent change, with optional 95% CI and exact
  comparator/dose/route/timepoint/population quotes.
- Schema v8 columns `effect_type`, `effect_size`, `effect_ci_low`,
  `effect_ci_high` and `quant` on `claims` and `fulltext_claims`, with an
  additive, idempotent v7→v8 migration.
- `ntx.quality` (`ntx-quality-v1`) with schema, provenance, effect and gold gates,
  a deterministic content hash and `ntx quality`.
- `ntx.dashboard` (`ntx dashboard`) with a self-contained HTML quality and
  reconciliation view.
- Effect-aware reconciliation report `ntx-fulltext-report-v3` with
  `effect_size_discrepancy`, `effect_type_mismatch`, context mismatches and an
  `effect_comparison` block.
- Effect metrics in evaluation (`claims_effect_strict`, type accuracy, value MAE,
  CI overlap, coverage, mismatches, bootstrap and by-stratum output) and effect
  fields in gold validation.
- `ntx db backup|verify|restore`, sidecar SHA-256/schema/counts, and `ntx doctor`.
- `/api/v1/quality` and `/api/v1/reconciliation`; `NTX_API_TOKEN` bearer auth and
  `NTX_API_ALLOW_HOSTS` Host allowlisting; non-loopback bind requires a token.

### Changed
- `Claim.extractor` defaults to `rules-v3`; `FeatureFlags.quantitative` is on by
  default.
- API non-GET methods are rejected after the same Host/token guard as GET.
- Coverage gate is 87 combined line+branch (90.02% line).

### Limits
- Effect extraction is heuristic, requires explicit labels and has no human gold.
- Default reports, discovery, calibration and ledger exports remain abstract-only.
- Full text remains local-only and is never redistributed.

## [0.14.0] - 2026-11-16

Claim-level reconciliation between the abstract and open-access full text. This
turns the v0.13 corroboration counters into auditable entity/outcome findings with
explicit reasons and severity, and lets evidence briefs opt into the comparison.

### Added
- `ntx.reconcile` with `REPORT_SCHEMA = "ntx-fulltext-report-v2"`, deterministic
  claim-level grouping, canonical content hashing and per-entity aggregation.
- Statuses `corroborated`, `direction_conflict`, `abstract_only`, `fulltext_only`
  and `indeterminate`.
- Descriptive reasons `direction_conflict`, `significance_conflict`,
  `sample_size_discrepancy`, `hedging_mismatch`, `discussion_only`,
  `abstract_sentence_in_fulltext` and `mixed_directions`.
- Severity levels `info`, `low`, `medium` and `high`; high is reserved for
  direction and significance conflicts.
- `ntx fulltext report --entity <id>` and
  `--fail-on {none,low,medium,high}`. `--fail-on-conflict` remains as an alias.
- `ntx report --with-fulltext` adds an optional reconciliation section to the
  Markdown and HTML briefs. It refuses to render when full-text provenance fails.
- Up to five exact-span claim references per side, with the full claim count kept
  separately, plus section and per-document counts.

### Changed
- Full-text report schema is now `ntx-fulltext-report-v2`; the terminal heading is
  `Full-text reconciliation`.
- The report verifies both abstract and full-text provenance before building.
- Default `ntx report` output, database schema v7 and ledger export v3 are
  unchanged. Full text remains local-only and opt-in.

### Not included
- Publisher PDFs, supplementary files, complete tables/figures, effect-size
  meta-analysis, automatic rubric changes, or automatic promotion of full-text
  claims into discovery, calibration or the ledger.
- Any claim that `corroborated` proves the abstract is correct or that a full-text
  sentence is true.

## [0.13.0] - 2026-11-16

Optional open-access full-text evidence. The abstract remains the default path, but
PMC papers can now be fetched, parsed into sections, and compared with their
abstract claims under the same exact-span invariant.

### Added
- `http.get_text` with HTTPS-only policy, `.txt` cache, offline mode, retry,
  throttling and byte caps.
- `ntx.fulltext` JATS parser that rejects `DOCTYPE`/`ENTITY`, reads body paragraphs
  and captions, records section offsets and caps XML/text size.
- `ntx fulltext fetch`, `ntx fulltext extract` and `ntx fulltext report`, plus
  `--doc-id`, `--limit`, `--offline`, `--json`, `--out` and
  `--fail-on-conflict`.
- Immutable `document_texts` snapshots with SHA-256, and `fulltext_claims` tied to
  the snapshot `text_hash`.
- Section-aware claim extraction through `extract_claims(..., section_labels=...)`;
  only Results, Discussion and Conclusions claims are stored.
- Corroboration statuses `corroborated`, `fulltext_only`, `abstract_only` and
  `direction_conflict`, with a canonical `ntx-fulltext-report-v1` content hash.
- `ntx check` verifies abstract and full-text provenance separately; abstract
  reports remain independent of full-text defects.

### Changed
- Database schema is now v7. Ledger export remains v3 and contains no full-text
  fields.
- README, `docs/FULLTEXT.md`, `docs/INTEGRITY.md`, `docs/ARCHITECTURE.md`,
  `docs/METHOD.md`, `docs/DATA_GOVERNANCE.md`, `SECURITY.md` and `CITATION.cff`
  document the full-text layer, its licences and its limits.

### Not included
- Publisher PDF scraping, supplementary files, figure/table cell extraction or
  redistribution of full text.
- Automatic promotion of full-text claims into default briefs, discovery,
  calibration or the ledger.
- Any claim that `corroborated` means the abstract is correct, that a full-text
  sentence is true, or that PMC coverage is complete.

## [0.12.0] - 2026-11-15

Read-only source verification and tamper-evident ledger checkpoints. The system can
now compare stored document metadata with Crossref/PubMed and chain ledger export
hashes across snapshots without changing the scientific record.

### Added
- `ntx verify` checks DOI (Crossref) or PMID (PubMed) metadata, with cached offline
  support, `--doc-id`, `--limit`, `--offline`, `--out`, `--json` and
  `--fail-on-drift`.
- Statuses `verified`, `metadata_mismatch`, `retraction_drift`,
  `local_retraction`, `correction`, `unverifiable` and `unavailable`; local
  retractions are never downgraded.
- Report schema `ntx-source-verification-v1` with canonical JSON, a SHA-256 content
  hash excluding `generated_at`, and per-document `claim_count`.
- `ntx ledger checkpoint` writes a `ntx-checkpoint-v1` file with sequence,
  previous hash and current ledger hash; it refuses to overwrite.
- `ntx ledger verify-checkpoints` checks hashes, continuous sequence, previous
  links and the current ledger, and exits non-zero on integrity failure.
- `docs/INTEGRITY.md`, tests for source status mapping, offline behavior, CLI
  output, checkpoint chaining and tamper detection.

### Changed
- README, `docs/LEDGER.md`, `docs/METHOD.md`, `docs/ARCHITECTURE.md`, `SECURITY.md`
  and `CITATION.cff` describe the integrity layer and its limits.
- Database schema remains v6 and ledger export remains v3.

### Not included
- Trusted timestamps, signatures, registry lookups for preregistration time,
  full-text verification or automatic evidence deletion.
- Any claim that a missing retraction notice proves validity or that a checkpoint
  proves a prediction preceded an experiment.

## [0.11.0] - 2026-11-14

Cluster-aware, auditable calibration report. The report is still descriptive
only, but it now treats repeated experiments for one hypothesis as dependent,
shows where the analysis is underpowered, and makes its assumptions inspectable.

### Added
- `--cluster hypothesis|experiment` (default `hypothesis`); the cluster bootstrap
  resamples whole hypotheses instead of rows.
- `n_clusters`, `underpowered_reasons` and `brier_skill_ci`; bootstrap intervals
  require both `n >= 10` and `n_clusters >= 10`.
- Subgroup summaries `by_kind` and `by_design`, shown only with at least 3 rows
  and 2 clusters.
- CI precision counts (`consistent`, `imprecise`, `inconsistent`, `missing`) and
  correction/outcome-record counts.
- Missingness by design (`registered`, `eligible`, `pending`, `excluded`,
  `eligible_fraction`).
- Fixed one-at-a-time sensitivity analyses (`first_outcome`, `qc_pass_only`,
  `precise_ci_only`) with deltas versus the primary analysis; no p-values or
  model selection.
- `--out` canonical JSON with `generated_at` and a SHA-256 `content_hash` that
  excludes the timestamp; `--html` self-contained escaped HTML with an inline SVG
  reliability plot.
- Report schema `ntx-calibration-v2`.
- Tests for cluster-bootstrap width, cluster shortfall, sensitivity variants, CI
  precision, missingness, hashing, HTML escaping and CLI file output.

### Changed
- `docs/CALIBRATION.md`, `docs/METHOD.md`, `docs/LEDGER.md`,
  `docs/ARCHITECTURE.md` and the README document clustering, sensitivities and
  the report artefacts.
- Database schema remains v6 and ledger export remains v3.

### Not included
- Hierarchical/Bayesian calibration, calibration slope/intercept, cluster-size
  adjustment, multiple-comparison correction or correction for selective
  reporting.
- Trusted timestamps, proof of preregistration timing or external source
  verification.
- Any probability, replication or treatment-effect claim.

## [0.10.0] - 2026-11-14

Descriptive calibration report for directional ledger predictions: reliability
bins, Brier skill, ECE/MCE, Murphy decomposition and deterministic bootstrap
intervals. The report never claims calibration, even above the underpowered
threshold.

### Added
- `ntx ledger calibration` with `--bins` (2–10), `--bootstrap` (0–5000) and
  `--json`; eligible rows and exclusions match `ledger score`.
- `ntx.calibration`: equal-width probability bins, bin-level predicted/observed
  rates, gaps and Brier scores, ECE/MCE and the binned Murphy identity.
- Deterministic percentile bootstrap for Brier and ECE when `n >= 10`, using fixed
  bin edges and seed 0.
- `underpowered` and `descriptive_only` flags, sparse-bin reporting, and warnings
  for small or selectively reported ledgers.
- `docs/CALIBRATION.md`: definitions, formulas, command, bootstrap caveat and safe
  interpretation.
- Tests for hand-computed Brier/decomposition, boundaries, invalid inputs,
  deterministic bootstrap, exclusions and CLI errors.

### Changed
- `docs/METHOD.md`, `docs/LEDGER.md`, `docs/ARCHITECTURE.md`, `docs/DISCOVERY.md`
  and the README describe the descriptive calibration layer.
- No schema change: the database remains v6 and ledger export remains v3.

### Not included
- Proof that predictions preceded experiments, external source verification or
  trusted registration time.
- Cluster bootstrap, correction for selective reporting, multiple comparisons or
  a rubric/replication probability.
- Any claim that a small ledger is calibrated.

## [0.9.0] - 2026-11-14

Registration and result-capture layer: reproducible two-arm power worksheets,
versioned registration manifests, and transactional external outcome import. These
features make planned sample size and imported results explicit without claiming
that an assumed effect exists or that an external source was verified.

### Added
- `ntx power` with `--test mean|proportion`, alpha, target power, one/two-sided
  testing and dropout; canonical `ntx-power-v1` worksheets with deterministic
  `PW-...` ids and a human/JSON output mode.
- `ntx.power` recomputes and validates stored worksheets; stale or tampered n
  values and ids are rejected.
- Schema v6 adds `experiments.power_json` and `outcomes.source_id` with a partial
  unique index per experiment/source; migration from v5 is additive and preserves
  old rows and append-only triggers.
- `ntx ledger register --file registration.json` for `ntx-registration-v1`
  manifests, with strict unknown-key rejection, `--power` attachment, `--dry-run`,
  conditional flag validation and a `power_check` shortfall warning.
- `ntx ledger outcome --source-id` and `ntx ledger import-outcomes` for JSON,
  JSONL or wrapper files; all experiment ids are preflighted, imports are one
  transaction, identical repeats skip, conflicting repeats fail, and duplicate
  source ids within a file are rejected.
- Ledger export schema v3 includes experiment power worksheets and outcome source
  ids in the content hash.
- `docs/REGISTRATION.md`: formulas, reference values, manifest fields, import
  semantics, limitations and trust boundary.
- Tests for power reference values, tamper rejection, schema v6 migration,
  manifest validation/dry-run/shortfall, import idempotence/conflicts/rollback and
  CLI exit codes.

### Changed
- Ledger export schema is now `ntx-ledger-v3`; consumers of v2 must accept the new
  `power` and `source_id` fields.
- `n_planned` is no longer described as a power calculation: it is a recorded
  choice, optionally checked against a worksheet, and a shortfall is only a
  warning.

### Not included
- Power for paired, crossover, cluster, time-to-event, ordinal or adaptive
  designs.
- Estimation of effect sizes from the corpus or prior outcomes.
- External registry lookup, DOI/source verification, trusted registration time or
  automatic outcome extraction.

## [0.8.0] - 2026-11-14

Explicit generation layer: user-supplied precursors, named cleavage rules,
truncation/alanine-scan analogs, and append-only lineage. Generated sequences are
hypothesis sources, not ranked or activity-predicted molecules.

### Added
- Schema v5 with append-only `precursors` and `candidate_lineage` tables and
  indexes; `UPDATE`/`DELETE` triggers reject edits. Migration from v4 is additive.
- `ntx precursor import|list|show`: transactional JSONL/JSON import, canonical
  residue and length 2–10,000 validation, optional UniProt/source metadata,
  content-derived `PR-...` ids, UTF-8 BOM support and duplicate rejection.
- `ntx generate cleavage`: trypsin, chymotrypsin, Lys-C, Arg-C and Glu-C rules
  with a proline exception; missed-cleavage enumeration; length filters; hard
  expansion cap that fails instead of silently truncating.
- `ntx generate analogs`: N/C truncations and single alanine substitutions,
  with target inheritance, no modification inheritance, and hard variant caps.
- `candidate lineage`: deterministic `L-...` rows with source, parent, operation,
  span, all cleavage sites/occurrence counts and parameters.
- Ledger export schema v2 includes precursors and lineage in the SHA-256 content
  hash.
- `docs/GENERATION.md`: rules, operations, caps, lineage and explicit non-claims.
- Tests for precursor validation/import, cleavage sites, missed cleavages,
  expansion caps, dry runs, analog enumeration, idempotence, schema v5 migration,
  append-only triggers, export and CLI output.

### Changed
- Version 0.8.0; 161 tests; coverage 86.78%. `docs/METHOD.md` gains §13 and
  renumbers version history to §14; README, architecture and discovery docs
  reflect the generation layer.

## [0.7.0] - 2026-11-14

Closed-loop verification: candidate import, append-only prediction/outcome
ledger, and directional Brier scoring. This release does not generate molecules
or claim that a small ledger is calibrated.

### Added
- Schema v4 with append-only `candidates`, `experiments`, `predictions` and
  `outcomes` tables; `UPDATE`/`DELETE` triggers reject edits. Migration from v3
  preserves existing claims.
- `ntx candidate import|list|show`: transactional JSONL/JSON import, canonical
  residue/length validation, known-target checks, content-derived `C-...` ids,
  `--dry-run`, and residue-table descriptors (MW, net charge pH 7.4, GRAVY,
  aromatic/cysteine counts, simple protease motifs), always marked heuristic.
- `ntx ledger register`: snapshots a discovered hypothesis, validates design,
  assay, primary outcome and probability in `[0.5, 1.0)`, and writes one
  experiment plus one write-once prediction.
- `ntx ledger outcome`: append-only outcomes with effect size, CI, n, QC and
  notes; corrections are new rows and the latest row is scored.
- `ntx ledger status|score|export`: pending/eligible/excluded counts, directional
  Brier score, skill versus 0.25, Wilson accuracy interval, per-kind/design
  descriptive groups, an underpowered flag below n=10, and a canonical export
  with a SHA-256 content hash that ignores `exported_at`.
- `docs/LEDGER.md`: protocol, scoring formula, exclusion policy, trust boundary
  and non-goals.
- Tests for migration, append-only enforcement, candidate validation, ledger
  transitions, hand-computed Brier values, exclusions and CLI JSON purity.

### Changed
- Version 0.7.0; 150 tests; coverage 86.68%. `docs/METHOD.md` gains §12 and
  renumbers version history to §13; `docs/ARCHITECTURE.md` and
  `docs/DISCOVERY.md` reflect the implemented ledger.

## [0.6.0] - 2026-11-14

Mechanistic layer: an explicitly hypothetical signed graph and qualitative
perturbation simulation. It does not claim to discover or validate a mechanism.

### Added
- `src/ntx/data/mechanisms.json`: curated ligand→receptor→pathway→process prior
  (22 nodes, 40 edges) with confidence and short reference labels.
- `ntx mechanism edges`: extracted entity→mechanism edges, aggregated by
  direction with doc support; prior-only edges are visible in `export`.
- `ntx mechanism simulate`: blocks one or more mechanism nodes, removes paths
  through them, and reports baseline/perturbed/delta **directions** per outcome.
  Output states that it is not kinetic or quantitative.
- `ntx mechanism check`: deterministic SHA-1 holdout (default 20%), graph built
  without held-out docs, agreement/disagreement/uncovered over held claims;
  `--holdout 0` is labelled in-sample.
- `ntx mechanism export`: graph JSON with nodes, signed edges, origin and doc ids.
- `ntx mechanism enrich`: opt-in UniProtKB metadata lookup through the normal
  HTTP cache; `--offline` uses cache only.
- Mechanism extraction: same-clause direction cue, negation veto, sign-0
  association edges for `via/through/associated`, overlap rejection for receptor
  names such as "substance P receptor", doc/sentence provenance, and
  entity-scoped mechanism→outcome edges so paths cannot be stitched across
  different peptides.
- `examples/mechanism_corpus.json`: runnable synthetic example, explicitly not a
  literature sample.

### Changed
- `docs/METHOD.md` section 11 and `docs/DISCOVERY.md` document the graph,
  simulation semantics and limitations.
- Version 0.6.0; 112 tests; coverage 85.49%.

## [0.5.0] - 2026-11-14

Audit scoring and gold-integrity gates: the two remaining ways a metric could be
computed from unchecked or unaudited data are now explicit and testable.

### Added
- `ntx audit score`: joins a completed verdict JSONL to claims and reports
  precision with a 95% Wilson interval, overall and by role/design.
  `unclear`/blank verdicts are excluded from the denominator and reported;
  missing claim ids are counted as a warning. `ntx.stats.wilson_interval` is the
  underlying stdlib-only implementation.
- `ntx audit sample --verdicts-out`: writes a blank machine-readable verdict
  template (`claim_id`, `doc_id`, `entity_id`, `start`, `verdict`) next to the
  human worksheet.
- Verdict loader validation: malformed JSON, unknown verdicts and duplicate
  `claim_id` values fail with a precise file/line error.
- Gold integrity in `ntx eval`: `gold_span_errors` (claim sentence or mention
  span does not match the stored text), `gold_missing_documents`, and capped
  `gold_error_examples`. `--strict-gold` exits 1 when either count is non-zero.
- Report lines for gold span errors and missing gold documents.

### Changed
- `docs/METHOD.md` and `README.md` document the integrity gate and the
  sample → verdict → score audit loop, including the Wilson interval.

## [0.4.0] - 2026-11-13

Gold-ready evaluation, regenerated model drafts, annotation operations and local
CI parity. Still rules-only: no LLM in the core, and no human gold metric is
claimed.

### Added
- Role-aware evaluation: role-strict claim P/R/F1, `role_gold_coverage`,
  role accuracy on strict matches, and role-mismatch examples. A role score is
  only meaningful when gold coverage is non-zero, which the report states.
- Deterministic claim error attribution: every strict FP/FN is classified as
  `entity_mismatch`, `outcome_mismatch`, `direction_mismatch`, `span_mismatch`
  or `sentence_mismatch`, with capped examples; role-strict bootstrap CI is added
  when gold roles exist.
- `--json` is now the only stdout for `ntx eval`, `ntx synthesize` and
  `ntx ablate`; human reports never contaminate machine output.
- Offline draft replay: `scripts/build_gold_v2.py --from-corpus` regenerates
  `gold/v2/draft.jsonl` from the frozen frame and local corpus without network,
  and fails clearly when the corpus is missing.
- Model draft v0.3 now carries role, section, evidence span, resolver and reasons:
  119 documents, 141 claims (139 intervention, 2 comparator); the artifact is
  reproducible byte-for-byte.
- `ntx gold conflicts`: field-level disagreement extraction with a blank
  adjudication skeleton (`value_a`/`value_b`); `gold merge` ignores unfinished
  blank rows instead of writing empty labels.
- `ntx audit sample`: deterministic role/design-stratified audit worksheet with
  sentence and evidence span for manual precision review.
- Annotator tool: displays section and evidence, validates span/sentence,
  enumerations and evidence containment before export, and exports the new fields.
- `scripts/ci.ps1` now matches CI: clean sdist+wheel build, install into a fresh
  venv outside the repository, demo/check, and byte-identical frozen report
  comparison.

### Changed
- `docs/METHOD.md` documents role metrics, error attribution, the audit workflow
  and the implemented ablation runner; `gold/PROTOCOL_V2.md` documents the
  conflict/adjudication flow and the enriched draft.

## [0.3.0] - 2026-11-13

Model v3 and extraction v2, a paired ablation runner, a design-weighted synthesis
view, and a read-only local API. Still rules-only: no LLM in the core.

### Added
- `ntx serve`: read-only local JSON API (`/api/v1/health`, `counts`, `entities`,
  `claims`, `gaps`, `synthesis`, `manifest/latest`) bound to `127.0.0.1`,
  SQLite read-only mode, paged and parameter-bound queries, no write methods.
- Optional `ntx.toml` configuration with typed validation and
  CLI > environment > file > default precedence; `ntx config` prints the
  effective values. Exit codes are now documented (0 ok, 1 integrity, 2 usage,
  3 configuration).
- `ntx check --json`.
- Single-transaction batch inserts in `process_documents` (≈1.8× faster on the
  119-document frame); see `docs/PERFORMANCE.md`.
- `ntx synthesize`: design-weighted evidence summary per entity–outcome with
  document-level bootstrap interval, human/animal split, and an explicit
  `fragile` flag. Comparator claims are excluded by default.
- `ntx ablate`: paired ablation of extraction feature flags on the same documents
  and gold, with a delta-F1 bootstrap interval; refuses any configuration that
  breaks provenance and warns when gold documents are absent from the corpus.
- `ntx.corpus` loader shared by evaluation and ablation tooling.
- Claim extraction v2: clause segmentation (`;`, `(n)`, `but`, `however`,
  `whereas`, `while`) determines the direction cue and the stored evidence span;
  entity role (`intervention`/`comparator`/`mechanism`), abstract section and
  explicit confidence reasons are recorded.
- Opt-in coreference resolver (`--resolve-coref`): a definite NP is linked to the
  single entity of the previous two sentences, with an explicit `antecedent`
  marker and abstention on ambiguity.
- SQLite schema v3 with additive migration: `role`, `evidence_start`,
  `evidence_end`, `section`, `resolver`, `reasons`; `ntx check` now validates the
  evidence sub-span as well as the sentence span.
- Report briefs show the evidence clause and the entity role; contradictions
  count only intervention claims.
- Gold annotation schema v2 (`ntx.annotation`) with strict span validation,
  agreement keys, adjudication overrides and annotation merging.
- `ntx gold validate|agreement|merge|progress|diff` commands, including frame
  progress by stratum and claim-level annotator diffs.
- Cohen's kappa and document-level bootstrap confidence intervals (`ntx.stats`),
  exposed through `ntx eval --bootstrap --frame` with per-stratum breakdown.
- Frozen, stratified 119-document gold-v2 sampling frame (`build_gold_v2.py`,
  `gold/v2/sampling_frame.json`) and model draft pre-annotations.
- Offline browser annotation tool (`gold/v2/annotator.html`).
- `gold/PROTOCOL_V2.md` and 28 additional tests (88 total, 85.7% coverage).
- Documented that the rules-v1 pilot metrics are historical: the pilot abstracts
  are not redistributed and the current extractor is rules-v2.

### Security
- HTTP responses are now HTTPS-only, size-capped (20 MB) and written to the cache
  atomically; non-HTTPS URLs are rejected before any I/O.
- Added `SECURITY.md` threat model and `docs/DATA_GOVERNANCE.md`.
- Added `docs/METHOD.md` with the pre-registered metric gates and ablation plan.
- Added `docs/MANUSCRIPT_OUTLINE.md` and `docs/RELEASE_CHECKLIST.md` with explicit
  TBD markers for results that require the human gold set.
- Added `scripts/profile_frame.py` and `docs/DESCRIPTIVE_RESULTS.md`: descriptive
  claim-yield, design, band and contradiction statistics for the frozen frame.

## [0.2.0] - 2026-11-13

### Added
- Installable package: ontology, query set and demo corpus now ship as package
  data, so `pip install ntx` works outside the repository.
- Reproducibility layer: deterministic rendering with `--frozen-time`, canonical
  JSON, and a run manifest (git SHA, ontology/config/corpus hashes, environment).
- Structured JSON logging and `run_id` propagation.
- Schema versioning via `PRAGMA user_version` with a `manifests` table and an
  idempotent v1→v2 migration.
- CI workflow, pre-commit hooks, Makefile, Dockerfile and `.env.example`.
- `ntx discover` hypothesis triage plus `docs/DISCOVERY.md`.
- Packaging, resource, determinism and migration tests.

### Changed
- Project layout, license split (MIT code / CC BY 4.0 docs and gold), citation
  metadata.

## [0.1.0] - 2026-11-12

### Added
- Initial prototype: ingestion (Europe PMC, PubMed, Crossref), lexicon-based
  normalization, claim extraction, appraisal, SQLite store, HTML/Markdown briefs,
  pilot gold evaluation and offline demo.
