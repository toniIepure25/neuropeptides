#!/usr/bin/env python3
"""Benchmark the deterministic offline demo and write a small JSON report."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ntx import __version__  # noqa: E402
from ntx.pipeline import run_demo  # noqa: E402
from ntx.store import Store  # noqa: E402


def measure(runs: int) -> dict[str, object]:
    timings: list[float] = []
    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for index in range(runs):
            db = Path(tmp) / ("bench-%d.db" % index)
            started = time.perf_counter()
            counts = run_demo(None, db)
            timings.append(time.perf_counter() - started)
            store = Store(db)
            try:
                counts = store.counts()
            finally:
                store.close()
    return {
        "package_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "runs": runs,
        "timings_seconds": [round(value, 4) for value in timings],
        "median_seconds": round(statistics.median(timings), 4),
        "counts": counts,
        "documents_per_second": round(counts["documents"] / statistics.median(timings), 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="number of fresh demo runs (default: 5)")
    parser.add_argument("--out", default="build/benchmark.json")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    report = measure(args.runs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
