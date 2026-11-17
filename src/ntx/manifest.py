"""Run manifests: what code, configuration, corpus and environment produced a result.

The manifest identity (``run_id``) is a content hash over everything except the
creation timestamp, so two runs with identical inputs and code share an id even
when executed at different times.
"""

from __future__ import annotations

import hashlib
import json
import platform as platform_mod
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import __version__, clock, config
from .store import SCHEMA_VERSION


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=config.project_root(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


@dataclass(frozen=True)
class Manifest:
    command: str
    code_version: str
    git_sha: str
    git_dirty: bool
    python: str
    platform: str
    config_hash: str
    corpus_hash: str
    schema_version: int
    created: str
    notes: str = ""

    def identity(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("created")
        return payload

    @property
    def run_id(self) -> str:
        return hashlib.sha256(canonical_json(self.identity()).encode("utf-8")).hexdigest()[:16]

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.identity()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["run_id"] = self.run_id
        return payload

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


def build(command: str, corpus_hash: str = "", notes: str = "") -> Manifest:
    sha = _git("rev-parse", "HEAD") or "unknown"
    dirty_output = _git("status", "--porcelain", "--untracked-files=no") or ""
    return Manifest(
        command=command,
        code_version=__version__,
        git_sha=sha,
        git_dirty=bool(dirty_output),
        python=sys.version.split()[0],
        platform=platform_mod.platform(),
        config_hash=config.resource_hash("ontology.json", "queries.json"),
        corpus_hash=corpus_hash,
        schema_version=SCHEMA_VERSION,
        created=clock.iso(),
        notes=notes,
    )


def write(manifest: Manifest, directory: Path | None = None) -> Path:
    target_dir = directory or Path(config.build_dir()) / "manifests"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / (manifest.run_id + ".json")
    path.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path
