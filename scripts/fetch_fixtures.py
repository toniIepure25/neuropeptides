"""Build the offline demo corpus from live Europe PMC (run once, commit the JSON).

Usage:
    python scripts/fetch_fixtures.py --per-compound 8 --out src/ntx/data/demo_corpus.json

The output file contains Europe PMC core records plus entity annotations. It is
the only network step needed to reproduce the demo; everything downstream runs
from this file with ``--offline``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("NTX_ROOT", str(ROOT))

from ntx import ingest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-compound", type=int, default=8)
    parser.add_argument("--out", default=str(ROOT / "src" / "ntx" / "data" / "demo_corpus.json"))
    parser.add_argument("--since", type=int, default=2015)
    args = parser.parse_args()

    cache = ROOT / "data" / "cache"
    queries = [
        "oxytocin AND (randomized OR trial OR cognition)",
        "BDNF AND (memory OR depression OR neuroplasticity)",
        '"neuropeptide Y" AND (feeding OR anxiety)',
        "Semax",
    ]
    results = []
    annotations = {}
    seen = set()
    for query in queries:
        payload = ingest.search(query, args.per_compound, cache)
        for record in (payload.get("resultList") or {}).get("result", []) or []:
            doc = ingest.doc_from_epmc(record)
            if doc.doc_id in seen or not doc.title:
                continue
            seen.add(doc.doc_id)
            results.append(record)
        pmids = [str(r.get("pmid")) for r in results if r.get("pmid")]
        annotations.update(ingest.annotations_for(pmids, cache))
        print("query=%r total=%d kept=%d" % (query, payload.get("hitCount"), len(results)))

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "meta": {
                    "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "source": "Europe PMC REST search resultType=core",
                    "queries": queries,
                    "per_compound": args.per_compound,
                },
                "results": results,
                "annotations": annotations,
            },
            fh,
            indent=1,
        )
    print("wrote %s (%d records, %d annotation sets)" % (target, len(results), len(annotations)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
