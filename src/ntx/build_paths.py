"""Stable paths for the CLI (kept separate to avoid import cycles).

``demo_corpus()`` returns ``None`` when no explicit corpus is configured: the
pipeline then reads the corpus bundled in the installed package.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import project_root


def root() -> Path:
    return project_root()


def default_db() -> Path:
    return Path(os.environ.get("NTX_DB", str(root() / "build" / "ntx.db")))


def demo_corpus() -> Path | None:
    override = os.environ.get("NTX_CORPUS")
    if override:
        return Path(override)
    return None
