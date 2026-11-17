# ntx

**Provenance-first evidence surveillance for neuropeptide and neurotrophic claims.**

`ntx` turns a bounded literature corpus into inspectable evidence: exact-span
claims, design and risk appraisal, gap hypotheses, design-weighted synthesis and
a local ledger for predictions. It is research software, not a clinical decision
tool and not a general-purpose chatbot.

<div class="grid cards" markdown>

- **Deterministic core.** Gazetteer and rule-based extraction; no LLM in the
  evidence path.
- **Fail closed.** A report will not render when an exact-span provenance check
  fails.
- **Local-first.** Abstracts, full text, databases and reports stay on your
  machine.
- **Text-free public gold.** Public annotations carry hashes, not verbatim
  sentences; hydration is explicit and verified.

</div>

## Install and run the demo

The runtime has **no third-party dependencies** beyond Python 3.11–3.13. Build or
download the wheel, then run it in a clean environment:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install .\ntx-0.18.0-py3-none-any.whl
.venv\Scripts\ntx --frozen-time demo --db build\demo.db --out build\reports
.venv\Scripts\ntx check --db build\demo.db
```

The bundled corpus contains 18 CC BY records with per-record attribution. The
demo is offline and deterministic under `--frozen-time`.

## What is validated

| Layer | Status |
| --- | --- |
| Code quality | 304 tests, line/branch coverage gates, Ruff, mypy |
| Provenance | Exact spans checked against stored text and snapshots |
| Reproducibility | Frozen-time demo; two reproducible builds; checksums |
| Human accuracy | **Not established**; no human-validated gold set yet |
| Clinical utility | **Not evaluated** and out of scope |

## Start here

- [Quick start and architecture](https://github.com/toniIepure25/neuropeptides/blob/main/README.md)
- [Command reference](CLI.md)
- [Method](METHOD.md)
- [Evaluation protocol and current limits](EVALUATION.md)
- [Architecture](ARCHITECTURE.md)
- [Local read-only API](API.md)
- [Data governance](DATA_GOVERNANCE.md)
- [Limitations](LIMITATIONS.md)
- [Contributing](https://github.com/toniIepure25/neuropeptides/blob/main/CONTRIBUTING.md)

## Citation and licence

Code is MIT; documentation is CC BY 4.0. See
[CITATION.cff](https://github.com/toniIepure25/neuropeptides/blob/main/CITATION.cff)
and [LICENSE](https://github.com/toniIepure25/neuropeptides/blob/main/LICENSE).
There is no DOI yet.
