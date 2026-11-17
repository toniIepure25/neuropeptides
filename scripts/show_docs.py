"""Print full title + abstract for given document ids (annotation helper)."""

from __future__ import annotations

import contextlib
import sqlite3
import sys
import textwrap
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "build" / "ntx.db"
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

conn = sqlite3.connect(str(DB))
for doc_id in sys.argv[1:]:
    row = conn.execute("SELECT doc_id, title, abstract FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    if not row:
        print("MISSING", doc_id)
        continue
    print("###", row[0])
    print(row[1])
    print()
    print(textwrap.fill(row[2] or "", 110))
    print()
