#!/usr/bin/env python3
"""Check relative links and image paths in the public Markdown tree."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"```.*?```", re.DOTALL)
SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#", "/")
PUBLIC_MARKDOWN = (
    "README.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "GOVERNANCE.md",
    "ROADMAP.md",
    "SUPPORT.md",
    "MAINTAINERS.md",
    "CHANGELOG.md",
)


def markdown_files(root: Path) -> list[Path]:
    files = [root / name for name in PUBLIC_MARKDOWN if (root / name).is_file()]
    for pattern in ("docs/**/*.md", "gold/**/*.md"):
        files.extend(sorted(root.glob(pattern)))
    return sorted(set(files))


def check_file(path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    for match in LINK.finditer(text):
        target = match.group(1).strip().split()[0].strip("<>")
        if not target or target.startswith(SKIP_PREFIXES):
            continue
        local = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not local:
            continue
        destination = (path.parent / local).resolve()
        if not destination.exists():
            line = text[: match.start()].count("\n") + 1
            try:
                displayed = path.relative_to(root).as_posix()
            except ValueError:
                displayed = path.as_posix()
            errors.append("%s:%d: missing target %s" % (displayed, line, target))
    return errors


def check(root: Path) -> list[str]:
    errors: list[str] = []
    for path in markdown_files(root):
        errors.extend(check_file(path, root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    errors = check(root)
    if errors:
        for error in errors:
            print("docs links: " + error, file=sys.stderr)
        return 1
    print("docs links: ok (%d files)" % len(markdown_files(root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
