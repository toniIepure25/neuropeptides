# Roadmap

This is a working roadmap, not a promise of dates. Items marked **external** need
a person, data agreement or identifier that cannot be produced by code alone.

## Current release — v0.18.0

- [x] Public, text-free gold and verified hydration.
- [x] Exact-span provenance checks for abstract and full-text claims.
- [x] Reproducible wheel and sdist builds with checksums.
- [x] OpenAPI contract for the local read-only API.
- [x] Documentation site with evaluation and limitations.
- [x] CodeQL, dependency review, Scorecard and SBOM.
- [ ] Branch protection, Pages and repository hardening applied after release.

## Next — evidence quality

- [ ] **External:** recruit two independent annotators for a double-annotated
  pilot; publish the protocol, agreement and adjudication.
- [ ] Add per-field agreement reporting for design, species and effect fields.
- [ ] Expand the sampling frame only when annotation capacity exists.
- [ ] Add regression fixtures for known extraction failure modes.

## Next — engineering

- [ ] Add a typed, documented Python facade for the stable read path.
- [ ] Add a bounded fuzz target for corpus loading and span validation.
- [ ] Add a reproducible performance benchmark to release notes.
- [ ] Evaluate a packaged SQLite extension only if it preserves the stdlib-only
  runtime constraint.

## External blockers

- **Human gold:** no human-validated precision/recall/F1 is claimed until double
  annotation and adjudication exist.
- **DOI:** no DOI is minted for this repository yet. Citation uses
  [CITATION.cff](CITATION.cff) and the release URL.
- **Clinical validation:** out of scope for this project.

## How items are chosen

Priority goes to changes that make a claim testable, a failure visible, or a
public artefact reproducible. Features that increase surface area without a
clear evidence path are deferred.
