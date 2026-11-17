"""Optional ``ntx.toml`` configuration with documented precedence.

Precedence is CLI flag > environment variable > config file > built-in default.
Only a small, typed surface is exposed; unknown keys are ignored so old config
files keep working, while invalid values fail loudly instead of silently falling
back.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .build_paths import default_db, root

CONFIG_FILENAME = "ntx.toml"


class SettingsError(ValueError):
    """Raised when a configuration file or environment value is invalid."""


@dataclass(frozen=True)
class Settings:
    db: str
    out: str
    cache: str
    host: str
    port: int
    bootstrap: int
    resolve_coref: bool

    def defaults(self) -> dict[str, Any]:
        """Map to argparse destination names used by the CLI."""
        return {
            "db": self.db,
            "out": self.out,
            "cache": self.cache,
            "host": self.host,
            "port": self.port,
            "bootstrap": self.bootstrap,
            "resolve_coref": self.resolve_coref,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_path(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit
    env = os.environ.get("NTX_CONFIG")
    if env:
        return Path(env)
    candidate = root() / CONFIG_FILENAME
    return candidate if candidate.is_file() else None


def _string(section: dict[str, Any], key: str, fallback: str, env: str) -> str:
    value: Any = os.environ.get(env, section.get(key, fallback))
    if not isinstance(value, str) or not value.strip():
        raise SettingsError("%s must be a non-empty string" % env)
    return value


def _integer(section: dict[str, Any], key: str, fallback: int, env: str, low: int, high: int) -> int:
    raw: Any = os.environ.get(env, section.get(key, fallback))
    if isinstance(raw, bool):
        raise SettingsError("%s must be an integer" % env)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise SettingsError("%s must be an integer" % env) from exc
    if value < low or value > high:
        raise SettingsError("%s must be between %d and %d" % (env, low, high))
    return value


def _boolean(section: dict[str, Any], key: str, fallback: bool, env: str) -> bool:
    if env in os.environ:
        raw = os.environ[env].strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        raise SettingsError("%s must be a boolean" % env)
    value = section.get(key, fallback)
    if not isinstance(value, bool):
        raise SettingsError("%s must be a boolean" % env)
    return value


def load_settings(path: Path | None = None) -> Settings:
    target = config_path(path)
    payload: dict[str, Any] = {}
    if target is not None:
        try:
            payload = tomllib.loads(Path(target).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SettingsError("config file not found: %s" % target) from exc
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError("invalid TOML in %s: %s" % (target, exc)) from exc
        if not isinstance(payload, dict):
            raise SettingsError("config root must be a table")

    paths = payload.get("paths", {})
    server = payload.get("server", {})
    evaluation = payload.get("eval", {})
    extraction = payload.get("extract", {})
    for name, section in (
        ("paths", paths),
        ("server", server),
        ("eval", evaluation),
        ("extract", extraction),
    ):
        if not isinstance(section, dict):
            raise SettingsError("[%s] must be a table" % name)

    return Settings(
        db=_string(paths, "db", str(default_db()), "NTX_DB"),
        out=_string(paths, "out", str(root() / "reports"), "NTX_REPORTS"),
        cache=_string(paths, "cache", str(root() / "data" / "cache"), "NTX_CACHE"),
        host=_string(server, "host", "127.0.0.1", "NTX_HOST"),
        port=_integer(server, "port", 8765, "NTX_PORT", 1, 65535),
        bootstrap=_integer(evaluation, "bootstrap", 2000, "NTX_BOOTSTRAP", 0, 100_000),
        resolve_coref=_boolean(extraction, "resolve_coref", False, "NTX_RESOLVE_COREF"),
    )
