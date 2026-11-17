#!/usr/bin/env python3
"""Enforce separate line, branch and combined coverage thresholds.

``coverage.py``'s ``fail_under`` applies to one combined number.  This gate keeps
the project's three explicit targets visible and fails with a specific message.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def measure(path: Path) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    totals = payload["totals"]
    statements = int(totals["num_statements"])
    branches = int(totals["num_branches"])
    line = 100.0 * int(totals["covered_lines"]) / statements if statements else 100.0
    branch = 100.0 * int(totals["covered_branches"]) / branches if branches else 100.0
    combined = (
        100.0 * (int(totals["covered_lines"]) + int(totals["covered_branches"])) / (statements + branches)
        if statements + branches
        else 100.0
    )
    return {"line": line, "branch": branch, "combined": combined}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("coverage_json", nargs="?", default="coverage.json")
    parser.add_argument("--line", type=float, default=93.0)
    parser.add_argument("--branch", type=float, default=85.0)
    parser.add_argument("--combined", type=float, default=90.0)
    args = parser.parse_args(argv)
    path = Path(args.coverage_json)
    if not path.is_file():
        print("coverage gate: %s not found (run pytest with --cov-report=json)" % path, file=sys.stderr)
        return 2
    actual = measure(path)
    thresholds = {"line": args.line, "branch": args.branch, "combined": args.combined}
    failed = []
    for name, threshold in thresholds.items():
        status = "ok" if actual[name] >= threshold else "FAIL"
        if status == "FAIL":
            failed.append(name)
        print("%-8s %6.2f%%  (threshold %.2f%%)  %s" % (name, actual[name], threshold, status))
    if failed:
        print("coverage gate failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
