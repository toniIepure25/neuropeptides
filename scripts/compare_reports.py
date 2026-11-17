"""Compare two rendered report directories byte-for-byte (reproducibility gate)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def digest(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: compare_reports.py <dir-a> <dir-b>", file=sys.stderr)
        return 2
    left, right = Path(argv[1]), Path(argv[2])
    a, b = digest(left), digest(right)
    if a == b:
        print("identical: %d files" % len(a))
        return 0
    only_left = sorted(set(a) - set(b))
    only_right = sorted(set(b) - set(a))
    differing = sorted(name for name in set(a) & set(b) if a[name] != b[name])
    for name in only_left:
        print("only in %s: %s" % (left, name))
    for name in only_right:
        print("only in %s: %s" % (right, name))
    for name in differing:
        print("differs: %s" % name)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
