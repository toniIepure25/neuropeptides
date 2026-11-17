"""Build a gold JSONL from sentence-substring annotations.

Each annotation names a document and a distinctive substring; the builder finds
the containing sentence, records its exact span, and emits one JSONL record per
document. Run after the demo database exists:

    python scripts/build_gold.py --annotations data/private_gold/pilot_annotations.json \
        --out build/pilot.full.jsonl

The annotations file contains verbatim snippets and is intentionally not part of
the public tree. Redact the output with ``ntx gold redact`` before committing.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("NTX_ROOT", str(ROOT))

from ntx.extract import split_sentences  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--annotations", default=str(ROOT / "data" / "private_gold" / "pilot_annotations.json")
    )
    parser.add_argument("--out", default=str(ROOT / "build" / "pilot.full.jsonl"))
    parser.add_argument("--db", default=str(ROOT / "build" / "ntx.db"))
    args = parser.parse_args()

    payload = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    by_doc: dict = {}
    missing = []
    for item in payload["items"]:
        row = conn.execute(
            "SELECT doc_id, title, abstract FROM documents WHERE doc_id=?", (item["doc_id"],)
        ).fetchone()
        if row is None:
            missing.append(item["doc_id"])
            continue
        text = (row["title"] + "\n\n" + row["abstract"]).strip()
        match = None
        for start, end in split_sentences(text):
            if item["contains"] in text[start:end]:
                match = (start, end)
                break
        if match is None:
            missing.append("%s :: %s" % (item["doc_id"], item["contains"]))
            continue
        entry = by_doc.setdefault(item["doc_id"], {"doc_id": item["doc_id"], "mentions": [], "claims": []})
        entry["claims"].append(
            {
                "entity_id": item["entity_id"],
                "direction": item["direction"],
                "outcome": item["outcome"],
                "sentence": text[match[0] : match[1]],
                "start": match[0],
                "end": match[1],
            }
        )

    with open(args.out, "w", encoding="utf-8") as fh:
        for doc_id in sorted(by_doc):
            fh.write(json.dumps(by_doc[doc_id], ensure_ascii=False) + "\n")

    print(
        "documents: %d | claims: %d | unresolved: %d"
        % (len(by_doc), sum(len(v["claims"]) for v in by_doc.values()), len(missing))
    )
    for entry in missing:
        print("  unresolved:", entry)
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
