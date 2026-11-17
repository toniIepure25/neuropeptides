#!/usr/bin/env python3
"""Check release metadata and public-data invariants before tagging."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPOSITORY = "https://github.com/toniIepure25/neuropeptides"
HEALTH_FILES = (
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "CITATION.cff",
    "CHANGELOG.md",
    "src/ntx/py.typed",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/workflows/release.yml",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _citation_value(text: str, key: str) -> str:
    match = re.search(r"^%s:\s*[\"']?([^\"'\n]+)" % re.escape(key), text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def check(root: Path, tag: str | None = None) -> list[str]:
    errors: list[str] = []
    version_text = _read(root / "src" / "ntx" / "__init__.py")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', version_text)
    if not match:
        return ["src/ntx/__init__.py: __version__ not found"]
    version = match.group(1)

    if tag is not None and tag.lstrip("v") != version:
        errors.append("tag %s does not match version %s" % (tag, version))

    citation = _read(root / "CITATION.cff")
    if _citation_value(citation, "version") != version:
        errors.append("CITATION.cff: version does not match %s" % version)
    if _citation_value(citation, "repository-code").rstrip("/") != REPOSITORY:
        errors.append("CITATION.cff: repository-code is not the public repository")
    if not _citation_value(citation, "date-released"):
        errors.append("CITATION.cff: missing date-released")

    changelog = _read(root / "CHANGELOG.md")
    if "## [%s]" % version not in changelog:
        errors.append("CHANGELOG.md: missing section for %s" % version)
    release_notes = root / "docs" / "releases" / ("v%s.md" % version)
    if not release_notes.is_file():
        errors.append("missing %s" % release_notes.relative_to(root))

    pyproject = _read(root / "pyproject.toml")
    if "example.invalid" in pyproject:
        errors.append("pyproject.toml: placeholder URL remains")
    if REPOSITORY not in pyproject:
        errors.append("pyproject.toml: public repository URL missing")

    for relative in HEALTH_FILES:
        if not (root / relative).is_file():
            errors.append("missing required repository file: %s" % relative)

    corpus_path = root / "src" / "ntx" / "data" / "demo_corpus.json"
    attribution_path = root / "src" / "ntx" / "data" / "ATTRIBUTION.md"
    try:
        corpus = json.loads(_read(corpus_path))
    except json.JSONDecodeError as exc:
        errors.append("demo_corpus.json: %s" % exc)
    else:
        records = corpus.get("results", [])
        bad = [
            str(record.get("pmid") or record.get("id") or "?")
            for record in records
            if str(record.get("license") or "").casefold() != "cc by"
        ]
        if bad:
            errors.append("demo corpus contains non-CC-BY records: %s" % ", ".join(bad[:5]))
        attribution = _read(attribution_path)
        missing = [
            str(record.get("pmid") or record.get("id") or "?")
            for record in records
            if str(record.get("pmid") or record.get("id") or "") not in attribution
        ]
        if missing:
            errors.append("ATTRIBUTION.md is missing records: %s" % ", ".join(missing[:5]))

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--tag", default=None, help="release tag to compare with the package version")
    args = parser.parse_args(argv)
    root = Path(args.root)
    errors = check(root, args.tag)
    if errors:
        for error in errors:
            print("release metadata: " + error, file=sys.stderr)
        return 1
    print("release metadata: ok (%s)" % root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
