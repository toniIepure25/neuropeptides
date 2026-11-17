# Release checklist

A release is a tagged commit plus a wheel, a source archive, checksums and
updated metadata. Nothing here depends on the gold set being complete; results
that need human gold stay `TBD`.

## 1. Before tagging

- [ ] `git status` is clean; no `data/`, `build/`, `reports/`, `dist/` content staged
- [ ] Version bumped in `src/ntx/__init__.py`
- [ ] `CHANGELOG.md` has a dated section for the version
- [ ] `CITATION.cff` version, date and repository URL match
- [ ] `docs/releases/v<version>.md` exists
- [ ] `README.md` status numbers match the latest gate run
- [ ] No third-party abstract/full-text sentences in tracked files
- [ ] Licences present: `LICENSE` (MIT), `LICENSE-DOCS` (CC BY 4.0)
- [ ] Demo attribution covers every bundled record
- [ ] `docs/releases/v<version>.md` includes SBOM and attestation notes
- [ ] `mkdocs build --strict` and `check_docs_links.py` pass
- [ ] OpenAPI paths match `serve.ROUTES` (`check_api_contract.py`)

## 2. Gates

```powershell
python scripts/ci.ps1
python scripts/check_release.py
python scripts/check_api_contract.py
python scripts/check_docs_links.py
python -m mkdocs build --strict
```

Must pass: ruff, ruff-format, mypy, pytest with line coverage ≥ 93, branch
coverage ≥ 85 and combined coverage ≥ 90 (`scripts/coverage_gate.py`), release
metadata checks, API-contract and documentation-link checks, strict docs build,
wheel build, wheel install in a clean venv outside the repository, demo +
provenance check, `ntx quality`, `ntx doctor`, redact/hydrate round-trip, and two
frozen-time runs byte-identical.

## 3. Artefacts

- [ ] `python -m build` produces sdist and wheel
- [ ] `python scripts/normalize_sdist.py dist/*.tar.gz --epoch 1794680589`
- [ ] Create a clean venv containing only the built wheel, remove `pip`, then run
  `python -m cyclonedx_py environment <venv> --pyproject (Resolve-Path pyproject.toml) --of JSON -o dist/ntx-<version>.sbom.json`
- [ ] Validate the SBOM: `bomFormat == "CycloneDX"` and the root component is
  `ntx` with no undeclared runtime dependencies.
- [ ] `pip install dist/ntx-<version>-py3-none-any.whl` in a fresh venv
- [ ] `ntx --version`, `ntx demo`, `ntx check` succeed from a directory outside
  the repository
- [ ] `docker build -t ntx:<version> .` succeeds and `docker run --rm ntx:<version>`
  prints the demo summary (if Docker is available)
- [ ] `ntx db backup`, `ntx db verify` and `ntx db restore` round-trip on the demo
- [ ] `ntx quality` and `ntx doctor` pass; API token/Host tests pass
- [ ] Reports from two `--frozen-time` runs compare identical

## 4. Publish

- [ ] Push `main`, then push the tag to the public remote
- [ ] `release.yml` creates the GitHub Release with wheel, sdist, SBOM and
  `SHA256SUMS`
- [ ] `gh attestation verify dist/*.whl --repo toniIepure25/neuropeptides`
  succeeds
- [ ] Verify the release assets and checksums
- [ ] Confirm the remote contains only the intended branches and tags
- [ ] Confirm the docs site is live and returns HTTP 200
- [ ] Confirm `main` protection requires the documented checks
- [ ] Archive the release on Zenodo (or an institutional repository) and mint a DOI
- [ ] Add the DOI to `CITATION.cff`, `README.md` and the manuscript
- [ ] Verify the DOI resolves and the archive contains the tagged tree

## 5. After release

- [ ] Open a `v<next>-dev` section in `CHANGELOG.md`
- [ ] Record the release in `docs/METHOD.md` if gates or metrics changed
- [ ] Re-run the gold workflow against the released version and store JSON
  outputs as supplementary material

## Known blockers

| Blocker | Effect | Owner |
| --- | --- | --- |
| Human gold v2 (30 docs double-annotated, adjudicated) | accuracy tables cannot be filled | annotators |
| DOI account / Zenodo archive | no citable DOI yet | maintainer |
| Docker daemon on the release machine | image digest unavailable | maintainer |
