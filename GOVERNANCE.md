# Governance

`ntx` is a maintainer-led research-software project. This document describes how
decisions are made, how changes are reviewed, and how security reports are
handled.

## Maintainer

The current maintainer is listed in [MAINTAINERS.md](MAINTAINERS.md). The
maintainer is responsible for releases, repository settings, security response
and the public data policy.

## Decision making

- Routine bug fixes, documentation and tests: maintainer review and green CI.
- Changes to the annotation schema, provenance invariants, database schema or
  release process: a short design note in `docs/adr/` or the pull request,
  followed by maintainer approval.
- Scientific claims: no accuracy or clinical claim is accepted without a
  traceable protocol, denominators and human adjudication where required.
- Security reports follow [SECURITY.md](SECURITY.md), not public issues.

Consensus is preferred. When consensus is not reached, the maintainer records
the decision and its rationale in the pull request or ADR.

## Contribution and review

- All changes use a pull request against `main` once branch protection is
  enabled.
- Required checks are listed in the repository branch protection settings and
  mirror the jobs in `.github/workflows/ci.yml`.
- Reviews focus on correctness, provenance, reproducibility, tests, data
  licensing and whether public documentation overstates the evidence.
- Commits should be coherent and use conventional prefixes where practical.

## Releases

- Releases follow [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md).
- A release requires a version bump, changelog entry, release notes, green CI,
  reproducible artifacts, checksums and provenance attestations.
- Published tags and release assets are immutable. Corrections are new
  releases.

## Public data

- Only redistributable records with explicit licences are committed.
- Verbatim third-party text and local gold annotations stay out of the public
  tree; public gold is text-free and hydrated locally.
- The demo corpus is selected for licence compliance, not scientific
  representativeness.

## Changes to this document

Governance changes use the same pull-request and review path as code. The
maintainer records material changes in the changelog.
