"""Basic database statistics for quick sanity checks."""

from __future__ import annotations

import contextlib
import sqlite3
import sys
from pathlib import Path

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = Path(__file__).resolve().parents[1] / "build" / "ntx.db"
conn = sqlite3.connect(str(DB))
print("direction:", dict(conn.execute("SELECT direction, COUNT(*) FROM claims GROUP BY 1").fetchall()))
print("outcome:  ", dict(conn.execute("SELECT outcome, COUNT(*) FROM claims GROUP BY 1").fetchall()))
print("band:     ", dict(conn.execute("SELECT band, COUNT(*) FROM appraisals GROUP BY 1").fetchall()))
print()
for direction, sentence in conn.execute(
    "SELECT direction, sentence FROM claims WHERE direction='none' LIMIT 8"
):
    print("[%s] %s" % (direction, sentence[:180]))
