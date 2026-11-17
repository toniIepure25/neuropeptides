"""Project paths and configuration loading (no third-party deps).

Bundled defaults live inside the package (``ntx/data``) so that an installed
wheel is self-contained. ``NTX_*`` environment variables override them, which
keeps the repository workflow (local ontology edits, custom corpora) unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
from importlib import resources
from pathlib import Path
from typing import Any

PACKAGE_DATA = "ntx.data"


def project_root() -> Path:
    """Repository or working directory that owns ``data/``, ``build/``, ``reports/``.

    Resolution order: ``NTX_ROOT``, the nearest ancestor containing
    ``pyproject.toml``, then the current working directory. This never depends on
    where the package is installed.
    """
    env = os.environ.get("NTX_ROOT")
    if env:
        return Path(env).resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def _subdir(env_name: str, default: str) -> Path:
    path = Path(os.environ.get(env_name, str(project_root() / default)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    return _subdir("NTX_DATA", "data")


def build_dir() -> Path:
    return _subdir("NTX_BUILD", "build")


def reports_dir() -> Path:
    return _subdir("NTX_REPORTS", "reports")


def read_resource(name: str) -> bytes:
    """Read a bundled data file as bytes, independent of installation layout."""
    return resources.files(PACKAGE_DATA).joinpath(name).read_bytes()


def resource_text(name: str) -> str:
    return read_resource(name).decode("utf-8")


def resource_json(name: str) -> dict[str, Any]:
    return json.loads(resource_text(name))


def resource_hash(*names: str) -> str:
    """Stable SHA-256 over the named bundled resources (for run manifests)."""
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(read_resource(name))
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _configured(name: str, env_name: str) -> dict[str, Any]:
    override = os.environ.get(env_name)
    if override:
        return load_json(Path(override))
    return resource_json(name)


def ontology() -> dict[str, Any]:
    return _configured("ontology.json", "NTX_ONTOLOGY")


def queries() -> dict[str, Any]:
    return _configured("queries.json", "NTX_QUERIES")
