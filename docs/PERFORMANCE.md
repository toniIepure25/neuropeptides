# Performance notes

Measured on the local Windows workstation (Python 3.13, NVMe SSD, stdlib only).
These are descriptive numbers for the current prototype, not service-level
commitments.

| Workload | Documents | Claims | Wall time | Throughput |
| --- | ---: | ---: | ---: | ---: |
| Gold-v2 frame, single transaction | 119 | 141 | 0.71 s | ≈169 docs/s |
| Gold-v2 frame, commit per document (before) | 119 | 141 | 1.26 s | ≈94 docs/s |
| Bundled demo, five fresh runs | 18 | 14 | see `build/benchmark.json` | descriptive |

Reproduce the demo measurement locally:

```powershell
python scripts/benchmark.py --runs 5 --out build\benchmark.json
```

The benchmark writes its Python version, platform, raw timings, median, counts
and documents per second. It is descriptive only and is not a CI threshold:
shared runners are too noisy for a meaningful wall-time gate.

The improvement comes from one explicit transaction per `process_documents`
call: `add_entity`, `add_document`, `add_mentions`, `add_claims` and
`add_appraisals` accept `commit=False` during the batch, and the pipeline
commits once at the end. Each method still commits by default, so interactive
and test callers keep the original behaviour.

Bottlenecks, in order:

1. Gazetteer matching and clause-aware extraction (pure Python regex/loops).
2. SQLite inserts (now amortized).
3. Report rendering (one file per document, only when requested).

The API is not benchmarked: it opens a read-only SQLite connection per request,
returns at most 200 claims, and synthesis bootstraps are bounded at 5000
resamples. For repeated analysis, build once with `ntx demo`/`ntx ingest` and
query the database directly.
