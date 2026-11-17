# Local read-only API

`ntx serve` exposes a built database over HTTP for inspection. It is a local
tool, not a public service: it binds to `127.0.0.1` by default, answers only
`GET`, and opens the database in SQLite read-only mode.

```powershell
ntx demo --db build/demo.db
ntx serve --db build/demo.db --port 8765
```

The startup line prints the URL. Stop with `Ctrl+C`.

## Endpoints

All responses are JSON (`application/json; charset=utf-8`) with sorted keys.

| Endpoint | Purpose |
| --- | --- |
| `GET /` | service name, version and endpoint index |
| `GET /api/v1/health` | status, package version, database schema version |
| `GET /api/v1/counts` | rows per table |
| `GET /api/v1/entities` | ontology entities and claim counts |
| `GET /api/v1/claims` | paged claims; filters `entity`, `outcome`; `limit` (1–200), `offset` |
| `GET /api/v1/gaps` | ranked hypotheses; `top` (1–50) |
| `GET /api/v1/synthesis` | design-weighted synthesis; `bootstrap` (0–5000) |
| `GET /api/v1/quality` | schema, provenance, effect and gold quality gates |
| `GET /api/v1/reconciliation` | claim-level abstract/full-text report; optional `entity` |
| `GET /api/v1/manifest/latest` | newest run manifest |
| `GET /api/v1/openapi.json` | OpenAPI 3.1 contract for every route |

Example:

```powershell
curl.exe "http://127.0.0.1:8765/api/v1/claims?entity=NP:OXT&limit=2"
```

```json
{
  "total": 9,
  "limit": 2,
  "offset": 0,
  "items": [
    {
      "claim_id": 1,
      "doc_id": "PMID:...",
      "entity_id": "NP:OXT",
      "direction": "increase",
      "outcome": "social_cognition",
      "role": "intervention",
      "evidence_start": 120,
      "evidence_end": 240,
      "reasons": ["explicit_entity", "design_detected", "p_value"],
      "...": "..."
    }
  ]
}
```

## OpenAPI contract

The server publishes an OpenAPI 3.1 document at `GET /api/v1/openapi.json`. The
document is available even when the database file is missing, so clients can
discover the contract before a build exists.

```powershell
curl.exe http://127.0.0.1:8765/api/v1/openapi.json
python scripts/check_api_contract.py
```

CI compares the document's paths and operation IDs with the server route table.
The security schemes are documented, but authentication applies only when
`NTX_API_TOKEN` is configured.

## Error contract

| Status | When |
| --- | --- |
| 200 | success |
| 400 | invalid query parameter (e.g. `limit=9999`) |
| 401 | token required or invalid (token configured) |
| 403 | Host not in the allowlist |
| 404 | unknown route |
| 405 | non-GET method |
| 500 | internal error (no traceback is returned) |
| 503 | database file missing |

## Security posture

- Host defaults to `127.0.0.1`. A non-loopback bind requires an `NTX_API_TOKEN`
  of at least 16 characters and fails with exit code 3 otherwise.
- When `NTX_API_TOKEN` is set, every request must send
  `Authorization: Bearer <token>` (or `X-API-Token`). Comparison is constant-time.
- `NTX_API_ALLOW_HOSTS` is a comma-separated Host allowlist; the default is
  `127.0.0.1,localhost,::1,[::1]`. Requests with another Host get `403`.
- Responses send `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, a restrictive `Content-Security-Policy`,
  `Cross-Origin-Resource-Policy: same-origin` and `Cache-Control: no-store`,
  including error responses.
- No CORS headers, no write endpoints, no file paths from user input beyond the
  configured database.
- SQL uses bound parameters; `limit` and `offset` are range-checked.
- `gaps`, `synthesis`, `quality` and `reconciliation` run over a read-only
  connection wrapper, so they cannot migrate or modify the database.
