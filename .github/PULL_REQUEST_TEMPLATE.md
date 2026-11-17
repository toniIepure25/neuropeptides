## Problem

<!-- What is broken or missing? Link the issue if one exists. -->

## Change

<!-- Describe the smallest useful change and any user-visible behaviour. -->

## Verification

<!-- Paste the exact commands you ran. -->

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m ruff format --check .`
- [ ] `python -m mypy src/ntx`
- [ ] `python scripts/check_release.py`
- [ ] `python scripts/check_api_contract.py`
- [ ] `python scripts/check_docs_links.py`
- [ ] `python -m mkdocs build --strict` (if docs changed)

## Scientific and data checklist

- [ ] Exact-span provenance is preserved; no silent span repair was added.
- [ ] No synthetic/model annotation is presented as human gold.
- [ ] No third-party abstract/full-text sentences, secrets, or personal data are
      added to the public tree.
- [ ] New public data has a documented licence and attribution.
- [ ] Default reports remain deterministic (`NTX_FROZEN_TIME` used in tests).
