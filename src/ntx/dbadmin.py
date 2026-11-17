"""Database backup, verification, restore, and operational diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import __version__, clock
from .store import SCHEMA_VERSION, Store

BACKUP_SCHEMA = "ntx-backup-v1"
COUNT_TABLES = ("documents", "entities", "mentions", "claims", "appraisals")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect("file:%s?mode=ro" % path.resolve().as_posix(), uri=True)
    try:
        out = {}
        for table in COUNT_TABLES:
            out[table] = int(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
        out["fulltext_documents"] = int(conn.execute("SELECT COUNT(*) FROM document_texts").fetchone()[0])
        out["fulltext_claims"] = int(conn.execute("SELECT COUNT(*) FROM fulltext_claims").fetchone()[0])
        return out
    finally:
        conn.close()


def _schema_version(path: Path) -> int:
    conn = sqlite3.connect("file:%s?mode=ro" % path.resolve().as_posix(), uri=True)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _integrity(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def sidecar_path(backup: Path) -> Path:
    return backup.with_name(backup.name + ".json")


class _ReadOnlyStore(Store):
    """Store analysis methods over a read-only connection."""

    def __init__(self, path: Path):
        uri = "file:%s?mode=ro" % path.resolve().as_posix()
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row
        self.path = path


def backup(db: Path, out: Path, *, force: bool = False) -> dict[str, Any]:
    """Copy a database with SQLite's backup API and write a sidecar manifest."""
    db, out = Path(db), Path(out)
    if not db.exists():
        raise FileNotFoundError("database not found: %s" % db)
    sidecar = sidecar_path(out)
    if (out.exists() or sidecar.exists()) and not force:
        raise FileExistsError("backup exists; pass --force to replace it: %s" % out)
    source_integrity = _integrity(db)
    if source_integrity != "ok":
        raise ValueError("source database integrity check failed: %s" % source_integrity)
    source_schema = _schema_version(db)
    source_hash = _sha256(db)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_name(out.name + ".tmp")
    source = sqlite3.connect(str(db))
    destination = sqlite3.connect(str(temp))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    copy_integrity = _integrity(temp)
    copy_counts = _counts(temp)
    copy_schema = _schema_version(temp)
    if copy_integrity != "ok" or copy_schema != source_schema:
        temp.unlink(missing_ok=True)
        raise ValueError(
            "backup copy verification failed: integrity=%s schema=v%d" % (copy_integrity, copy_schema)
        )
    os.replace(temp, out)
    payload = {
        "schema": BACKUP_SCHEMA,
        "version": __version__,
        "source": str(db),
        "created": clock.iso(),
        "sha256": _sha256(out),
        "bytes": out.stat().st_size,
        "schema_version": copy_schema,
        "counts": copy_counts,
        "integrity_check": copy_integrity,
        "source_sha256": source_hash,
        "source_schema_version": source_schema,
        "copy_verified": True,
    }
    sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify(db: Path, sidecar: Path | None = None) -> dict[str, Any]:
    """Verify a database against its sidecar hash, schema, counts and integrity."""
    db = Path(db)
    sidecar = Path(sidecar) if sidecar else sidecar_path(db)
    checks: list[dict[str, Any]] = []
    if not db.exists():
        return {
            "ok": False,
            "checks": [{"name": "exists", "status": "fail", "detail": "database not found"}],
        }
    if not sidecar.exists():
        return {
            "ok": False,
            "checks": [{"name": "sidecar", "status": "fail", "detail": "sidecar not found: %s" % sidecar}],
        }
    try:
        expected = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "ok": False,
            "checks": [{"name": "sidecar", "status": "fail", "detail": "sidecar is not valid JSON"}],
        }
    if not isinstance(expected, dict):
        return {
            "ok": False,
            "checks": [{"name": "sidecar", "status": "fail", "detail": "sidecar is not an object"}],
        }
    checks.append(
        {
            "name": "sidecar_schema",
            "status": "pass" if expected.get("schema") == BACKUP_SCHEMA else "fail",
            "detail": str(expected.get("schema") or "missing"),
        }
    )
    actual_hash = _sha256(db)
    expected_hash = str(expected.get("sha256") or "")
    checks.append(
        {
            "name": "sha256",
            "status": "pass" if expected_hash and actual_hash == expected_hash else "fail",
            "detail": actual_hash,
        }
    )
    expected_bytes = expected.get("bytes")
    checks.append(
        {
            "name": "bytes",
            "status": "pass" if expected_bytes == db.stat().st_size else "fail",
            "detail": "%d (expected %s)" % (db.stat().st_size, expected_bytes),
        }
    )
    schema_version = _schema_version(db)
    counts = _counts(db)
    checks.append(
        {
            "name": "schema",
            "status": "pass" if schema_version == expected.get("schema_version") else "fail",
            "detail": "v%d (expected v%s)" % (schema_version, expected.get("schema_version")),
        }
    )
    checks.append(
        {
            "name": "counts",
            "status": "pass" if counts == expected.get("counts") else "fail",
            "detail": json.dumps(counts, sort_keys=True),
        }
    )
    integrity = _integrity(db)
    checks.append(
        {
            "name": "integrity",
            "status": "pass" if integrity == "ok" else "fail",
            "detail": integrity,
        }
    )
    return {"ok": all(check["status"] == "pass" for check in checks), "checks": checks}


def restore(backup_path: Path, db: Path, *, force: bool = False) -> dict[str, Any]:
    """Restore a verified backup to ``db``; existing databases require ``force``."""
    backup_path, db = Path(backup_path), Path(db)
    if not backup_path.exists():
        raise FileNotFoundError("backup not found: %s" % backup_path)
    verification = verify(backup_path)
    if not verification["ok"]:
        raise ValueError("backup verification failed: %s" % verification["checks"])
    if db.exists() and not force:
        raise FileExistsError("destination exists; pass --force to replace it: %s" % db)
    db.parent.mkdir(parents=True, exist_ok=True)
    temp = db.with_name(db.name + ".restore.tmp")
    source = sqlite3.connect(str(backup_path))
    destination = sqlite3.connect(str(temp))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    expected = json.loads(sidecar_path(backup_path).read_text(encoding="utf-8"))
    copy_integrity = _integrity(temp)
    copy_schema = _schema_version(temp)
    copy_counts = _counts(temp)
    if (
        copy_integrity != "ok"
        or copy_schema != expected.get("schema_version")
        or copy_counts != expected.get("counts")
    ):
        temp.unlink(missing_ok=True)
        raise ValueError(
            "restored copy verification failed: integrity=%s schema=v%d counts=%s"
            % (copy_integrity, copy_schema, copy_counts)
        )
    os.replace(temp, db)
    return {
        "restored": str(db),
        "schema_version": copy_schema,
        "counts": copy_counts,
        "verified": True,
    }


def doctor(db: Path) -> dict[str, Any]:
    """Run operational checks that are safe on a live read-only database."""
    db = Path(db)
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "exists",
            "status": "pass" if db.exists() else "fail",
            "detail": str(db),
        }
    )
    if not db.exists():
        return {"ok": False, "checks": checks}
    integrity = _integrity(db)
    checks.append(
        {
            "name": "integrity",
            "status": "pass" if integrity == "ok" else "fail",
            "detail": integrity,
        }
    )
    store = _ReadOnlyStore(db)
    try:
        schema_version = store.schema_version
        checks.append(
            {
                "name": "schema",
                "status": "pass" if schema_version == SCHEMA_VERSION else "fail",
                "detail": "v%d (expected v%d)" % (schema_version, SCHEMA_VERSION),
            }
        )
        abstract_errors = store.provenance_errors()
        fulltext_errors = store.fulltext_provenance_errors()
        counts = store.counts()
    finally:
        store.close()
    checks.append(
        {
            "name": "provenance",
            "status": "pass" if not abstract_errors else "fail",
            "detail": "%d abstract error(s)" % len(abstract_errors),
        }
    )
    checks.append(
        {
            "name": "fulltext_provenance",
            "status": "pass" if not fulltext_errors else "fail",
            "detail": "%d full-text error(s)" % len(fulltext_errors),
        }
    )
    checks.append(
        {
            "name": "counts",
            "status": "pass",
            "detail": json.dumps(counts, sort_keys=True),
        }
    )
    return {"ok": all(check["status"] == "pass" for check in checks), "checks": checks}


def format_checks(payload: dict[str, Any]) -> str:
    lines = ["ok: %s" % ("yes" if payload["ok"] else "no")]
    for check in payload["checks"]:
        lines.append("%-22s %-5s %s" % (check["name"], check["status"], check["detail"]))
    return "\n".join(lines)
