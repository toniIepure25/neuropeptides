# Contributing to ntx

`ntx` is research software for provenance-first evidence surveillance. Contributions
are welcome, but changes that affect extraction, scoring or reporting must keep the
scientific invariants below intact.

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pre-commit install
```

Run the same gates as CI:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy src/ntx
python -m pytest --cov=ntx --cov-branch --cov-report=term-missing --cov-report=json:coverage.json
python scripts/coverage_gate.py coverage.json --line 93 --branch 85 --combined 90
python scripts/check_release.py
```

## Scientific invariants

1. **Exact spans.** For every stored claim, `text[start:end]` must equal the
   recorded sentence. Do not add a fallback that silently repairs a broken span.
2. **No fabricated gold.** Draft/model annotations are not human gold. Do not
   report precision, recall or F1 as validated without human annotation.
3. **No third-party text in public artefacts.** Gold files are committed as
   redacted labels plus SHA-256 hashes. Full sentences stay in the local,
   git-ignored corpus and are restored with `ntx gold hydrate`.
4. **Determinism.** Default reports must remain byte-identical for the same
   frozen input. Use `NTX_FROZEN_TIME` in new tests.
5. **Local-first privacy.** Do not add telemetry, remote assets to reports, or
   uploads of abstracts/full text.

## Data and licence rules

- The bundled demo corpus may contain only records with an explicit `cc by`
  licence, listed in `src/ntx/data/ATTRIBUTION.md`.
- Never commit `data/`, `reports/`, `build/` or a SQLite database containing
  third-party abstracts or full text.
- Redacted gold changes must be generated with `ntx gold redact`; do not
  hand-edit hashes.
- If you add a new public data source, document its licence and attribution.

## Commit and pull-request style

Use imperative, scoped commit messages such as:

```text
feat(gold): add text-free annotation hydration
fix(api): reject short tokens on non-loopback binds
docs: clarify full-text redistribution policy
```

A pull request should include:

- a short problem statement and the exact commands used to verify it;
- tests for changed behaviour;
- documentation updates for user-visible changes;
- confirmation that no third-party text, secrets or personal data are included.

## Reporting security issues

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability
reporting for this repository, as described in [SECURITY.md](SECURITY.md).

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
