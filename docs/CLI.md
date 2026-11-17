# Command reference

All commands are available through `python -m ntx` or the installed `ntx`
entry point. `ntx --help` and `ntx <command> --help` are authoritative for the
current release.

## Global options

| Option | Meaning |
| --- | --- |
| `--version` | Print the package version. |
| `--frozen-time` | Freeze timestamps for byte-identical output. |
| `--log-level` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `--config` | Path to an `ntx.toml` configuration file. |

## Build and inspect

```powershell
ntx demo --db build\demo.db --out build\reports
ntx from-fixture --corpus corpus.json --db build\run.db
ntx ingest --query "oxytocin social cognition" --db build\run.db
ntx report --db build\run.db --out build\reports
ntx dashboard --db build\run.db --out build\dashboard.html
ntx quality --db build\run.db --out build\quality.json
ntx check --db build\run.db
ntx doctor --db build\run.db
ntx config
```

`ntx check` verifies abstract and full-text exact spans. `ntx doctor` checks
database integrity, schema version and provenance, and is safe to run in CI.

## Evidence and synthesis

```powershell
ntx discover --db build\run.db --top 20 --out build\gaps.json
ntx synthesize --db build\run.db --bootstrap 200 --out build\synthesis.json
ntx mechanism --db build\run.db --out build\mechanism.json
ntx fulltext fetch --db build\run.db
ntx fulltext extract --db build\run.db
ntx fulltext report --db build\run.db --out build\reconciliation.json
ntx verify --db build\run.db --out build\metadata.json
```

These commands are descriptive. Absence of a retraction notice is not proof of
validity, and synthesis intervals are not causal estimates.

## Hypotheses, candidates and the ledger

```powershell
ntx candidate import --file candidates.json --db build\run.db
ntx precursor import --file precursors.json --db build\run.db
ntx generate cleavage --db build\run.db --precursor PREC:1
ntx power --test mean --effect-size 0.5 --alpha 0.05 --target-power 0.8
ntx ledger register --db build\run.db --file prediction.json
ntx ledger outcome --db build\run.db --experiment EXP:1 --observed increase
ntx ledger score --db build\run.db
ntx ledger export --db build\run.db --out build\ledger.json
```

The ledger is append-only through the supported API. Corrections are new rows,
not edits.

## Annotation and evaluation

```powershell
ntx gold validate --file gold\pilot.jsonl
ntx gold agreement --a annotator_a.jsonl --b annotator_b.jsonl
ntx gold merge --primary a.jsonl --secondary b.jsonl --out adjudicated.jsonl
ntx gold redact --in gold.full.jsonl --corpus corpus.json --out gold.public.jsonl
ntx gold hydrate --in gold.public.jsonl --corpus corpus.json --out gold.full.jsonl
ntx eval --db build\run.db --gold gold.full.jsonl
ntx ablate --db build\run.db --gold gold.full.jsonl
ntx audit sample --db build\run.db --n 20
ntx audit score --verdicts build\audit.jsonl
```

`ntx eval` rejects redacted gold. `ntx gold hydrate` verifies every hash and
span before writing restored text.

## Local API

```powershell
ntx demo --db build\demo.db
ntx serve --db build\demo.db --port 8765
```

The API is read-only and binds to loopback by default. Its OpenAPI document is
served at `/api/v1/openapi.json`; see [API](API.md).

## Maintenance

```powershell
ntx db backup --db build\run.db --out build\backup.db
ntx db verify --db build\backup.db
ntx db restore --backup build\backup.db --db build\restored.db
```

Commands fail closed with a non-zero exit code and a short diagnostic on stderr;
run `ntx <command> --help` for command-specific options.
