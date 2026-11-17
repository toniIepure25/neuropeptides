"""Read-only local JSON API over a built ntx database.

The server is intentionally small and local-first: it binds to 127.0.0.1 by
default, opens the database in SQLite read-only mode, exposes only GET endpoints,
and never writes to the database or the filesystem. It is a way to inspect a run,
not a public service.
"""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, quote, urlsplit

from . import __version__, discovery, quality, reconcile, synthesis
from .store import SCHEMA_VERSION, Store

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
MIN_NON_LOOPBACK_TOKEN = 16
ROUTES = {
    "/api/v1/health",
    "/api/v1/counts",
    "/api/v1/entities",
    "/api/v1/claims",
    "/api/v1/gaps",
    "/api/v1/synthesis",
    "/api/v1/quality",
    "/api/v1/reconciliation",
    "/api/v1/manifest/latest",
}


def _connect(path: Path) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % quote(path.resolve().as_posix(), safe="/:")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


class _ReadOnlyStore(Store):
    """Store methods bound to a read-only connection without running migrations."""

    def __init__(self, conn: sqlite3.Connection, path: Path):
        self.conn = conn
        self.path = path


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("reasons", "bias_flags"):
        if key in item and isinstance(item[key], str):
            try:
                item[key] = json.loads(item[key])
            except json.JSONDecodeError:
                item[key] = []
    if "quant" in item and isinstance(item["quant"], str):
        try:
            item["quant"] = json.loads(item["quant"])
        except json.JSONDecodeError:
            item["quant"] = {}
    return item


def _int_param(query: dict[str, list[str]], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = query.get(name, [""])[0]
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("%s must be an integer" % name) from exc
    if value < minimum or value > maximum:
        raise ValueError("%s must be between %d and %d" % (name, minimum, maximum))
    return value


class _Handler(BaseHTTPRequestHandler):
    server_version = "ntx/" + __version__
    db_path: Path = Path("build/ntx.db")
    api_token: str = ""
    allowed_hosts: frozenset[str] = frozenset(LOOPBACK_HOSTS)

    def log_message(self, fmt: str, *args: Any) -> None:
        return  # keep stdout/stderr clean; the CLI logs lifecycle events

    def _guard(self) -> bool:
        host_header = self.headers.get("Host", "")
        hostname = urlsplit("//" + host_header).hostname
        if not hostname or hostname.casefold() not in self.allowed_hosts:
            self._error(
                HTTPStatus.FORBIDDEN,
                "host not allowed; set NTX_API_ALLOW_HOSTS",
            )
            return False
        if self.api_token:
            auth = self.headers.get("Authorization", "")
            supplied = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not supplied:
                supplied = self.headers.get("X-API-Token", "")
            if not supplied or not hmac.compare_digest(supplied, self.api_token):
                self._error(
                    HTTPStatus.UNAUTHORIZED,
                    "bearer token required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return False
        return True

    # ------------------------------------------------------------------ output
    def _send(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _error(
        self,
        status: int,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps({"error": message, "status": int(status)}, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    # -------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if not self._guard():
            return
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        if route == "/":
            self._send(
                {
                    "name": "ntx read-only API",
                    "version": __version__,
                    "schema_version": SCHEMA_VERSION,
                    "endpoints": [
                        "/api/v1/health",
                        "/api/v1/counts",
                        "/api/v1/entities",
                        "/api/v1/claims?entity=&outcome=&limit=&offset=",
                        "/api/v1/gaps?top=",
                        "/api/v1/synthesis?bootstrap=",
                        "/api/v1/quality",
                        "/api/v1/reconciliation?entity=",
                        "/api/v1/manifest/latest",
                    ],
                }
            )
            return
        if not route.startswith("/api/v1/"):
            self._error(HTTPStatus.NOT_FOUND, "unknown route")
            return
        if route not in ROUTES:
            self._error(HTTPStatus.NOT_FOUND, "unknown route")
            return
        if not self.db_path.exists():
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "database not found: %s" % self.db_path)
            return
        try:
            payload = self._dispatch(route, query)
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")
            return
        self._send(payload)

    def do_POST(self) -> None:  # noqa: N802
        if self._guard():
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "read-only API; use GET")

    def do_PUT(self) -> None:  # noqa: N802
        if self._guard():
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "read-only API; use GET")

    def do_DELETE(self) -> None:  # noqa: N802
        if self._guard():
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "read-only API; use GET")

    # --------------------------------------------------------------- dispatch
    def _dispatch(self, route: str, query: dict[str, list[str]]) -> Any:
        conn = _connect(self.db_path)
        try:
            if route == "/api/v1/health":
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                return {
                    "status": "ok",
                    "version": __version__,
                    "schema_version": int(version),
                    "database": str(self.db_path),
                }
            if route == "/api/v1/counts":
                counts = {}
                for table in ("documents", "entities", "mentions", "claims", "appraisals"):
                    counts[table] = int(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
                return counts
            if route == "/api/v1/entities":
                rows = conn.execute(
                    "SELECT e.entity_id, e.pref_name, e.class, COUNT(c.claim_id) AS claims"
                    " FROM entities e LEFT JOIN claims c ON c.entity_id = e.entity_id"
                    " GROUP BY e.entity_id ORDER BY claims DESC, e.entity_id"
                )
                return {"items": [dict(row) for row in rows]}
            if route == "/api/v1/claims":
                limit = _int_param(query, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
                offset = _int_param(query, "offset", 0, 0, 10_000_000)
                clauses = []
                params: list[Any] = []
                entity = query.get("entity", [""])[0]
                outcome = query.get("outcome", [""])[0]
                if entity:
                    clauses.append("entity_id = ?")
                    params.append(entity)
                if outcome:
                    clauses.append("outcome = ?")
                    params.append(outcome)
                where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
                total = int(conn.execute("SELECT COUNT(*) FROM claims" + where, params).fetchone()[0])
                rows = conn.execute(
                    "SELECT * FROM claims" + where + " ORDER BY claim_id LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                )
                return {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "items": [_row_to_dict(row) for row in rows],
                }
            if route == "/api/v1/gaps":
                top = _int_param(query, "top", 10, 1, 50)
                store = cast(Store, _ReadOnlyStore(conn, self.db_path))
                hypotheses = discovery.generate(store, top=top)
                return {"items": [item.to_dict() for item in hypotheses]}
            if route == "/api/v1/synthesis":
                n_boot = _int_param(query, "bootstrap", 200, 0, 5000)
                store = cast(Store, _ReadOnlyStore(conn, self.db_path))
                summaries = synthesis.synthesize(store, n_boot=n_boot)
                return {"items": summaries}
            if route == "/api/v1/quality":
                store = cast(Store, _ReadOnlyStore(conn, self.db_path))
                return quality.build_quality(store)
            if route == "/api/v1/reconciliation":
                store = cast(Store, _ReadOnlyStore(conn, self.db_path))
                entity_filter: str | None = query.get("entity", [""])[0] or None
                return reconcile.build_report(store, entity_id=entity_filter)
            if route == "/api/v1/manifest/latest":
                row = conn.execute("SELECT * FROM manifests ORDER BY created DESC LIMIT 1").fetchone()
                if row is None:
                    return {"manifest": None}
                try:
                    payload = json.loads(row["payload"])
                except json.JSONDecodeError:
                    payload = {}
                return {"run_id": row["run_id"], "created": row["created"], "manifest": payload}
            raise ValueError("unknown route")
        finally:
            conn.close()


def make_server(
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    token: str | None = None,
    allow_hosts: frozenset[str] | set[str] | None = None,
) -> ThreadingHTTPServer:
    if token is None:
        token = os.environ.get("NTX_API_TOKEN", "")
    if allow_hosts is None:
        raw_hosts = os.environ.get("NTX_API_ALLOW_HOSTS", "")
        allow_hosts = (
            frozenset(item.strip().casefold() for item in raw_hosts.split(",") if item.strip())
            if raw_hosts.strip()
            else frozenset(LOOPBACK_HOSTS)
        )
    allowed = frozenset(str(item).casefold() for item in allow_hosts)
    if host.casefold() not in LOOPBACK_HOSTS:
        if not token:
            raise PermissionError("non-loopback bind requires NTX_API_TOKEN")
        if len(token) < MIN_NON_LOOPBACK_TOKEN:
            raise PermissionError(
                "non-loopback NTX_API_TOKEN must be at least %d characters" % MIN_NON_LOOPBACK_TOKEN
            )
    handler = type(
        "NtxHandler",
        (_Handler,),
        {
            "db_path": Path(db_path),
            "api_token": token,
            "allowed_hosts": allowed,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(db_path: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = make_server(db_path, host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
