"""Show missed and extra claims for a gold file (error analysis)."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("NTX_ROOT", str(ROOT))

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default=str(ROOT / "gold" / "pilot.jsonl"))
    parser.add_argument("--db", default=str(ROOT / "build" / "ntx.db"))
    args = parser.parse_args()

    gold = [
        json.loads(line) for line in Path(args.gold).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    for item in gold:
        doc_id = item["doc_id"]
        print("=" * 100)
        print(doc_id)
        gold_keys = {(c["entity_id"], c["direction"], c["outcome"], c["start"]) for c in item["claims"]}
        pred = [
            dict(r)
            for r in conn.execute(
                "SELECT entity_id, direction, outcome, start, sentence FROM claims WHERE doc_id=?", (doc_id,)
            )
        ]
        pred_keys = {(r["entity_id"], r["direction"], r["outcome"], r["start"]) for r in pred}
        print("  gold only:")
        for claim in item["claims"]:
            key = (claim["entity_id"], claim["direction"], claim["outcome"], claim["start"])
            if key not in pred_keys:
                print(
                    "    - %s/%s/%s :: %s"
                    % (claim["entity_id"], claim["direction"], claim["outcome"], claim["sentence"][:150])
                )
        print("  predicted only:")
        for row in pred:
            key = (row["entity_id"], row["direction"], row["outcome"], row["start"])
            if key not in gold_keys:
                print(
                    "    + %s/%s/%s :: %s"
                    % (row["entity_id"], row["direction"], row["outcome"], row["sentence"][:150])
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
