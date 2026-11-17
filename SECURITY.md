# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities through GitHub's
[private vulnerability reporting](https://github.com/toniIepure25/neuropeptides/security/advisories/new).
Do not open a public issue before a coordinated fix is available, and do not
include abstracts, full text, credentials, or personal data in a report.

Include the affected version, a minimal reproduction, the impact you believe is
possible, and any suggested mitigation. This is a single-maintainer research
project; reports are handled on a best-effort basis without a paid SLA.

## Supported versions

Security fixes target the latest released minor version. Older releases may be
asked to upgrade rather than receive a backport.

## What the software is

`ntx` is a local-first research tool. The default CLI writes a local SQLite
database and static reports. The optional read-only HTTP API serves that local
database; it is not a multi-user service and does not provide user accounts,
roles, or tenant isolation.

## Security controls

| Area | Control |
| --- | --- |
| HTTP exposure | Loopback bind by default. Non-loopback binds require `NTX_API_TOKEN` of at least 16 characters and enforce a `Host` allowlist. |
| API authentication | Bearer token comparison uses `hmac.compare_digest`; the token is never written to reports. |
| Response headers | API responses set `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, and a restrictive Content-Security-Policy. |
| Provenance | `text[start:end] == sentence` is checked before reports render; forged spans fail closed. |
| HTML output | Interpolated values are escaped; reports are self-contained and load no remote assets. |
| SQL | All queries use parameter binding. |
| Network input | Only HTTPS sources are accepted; response size is capped before parsing. |
| XML | JATS `DOCTYPE`/`ENTITY` declarations are rejected before parsing. |
| Local state | Caches are written atomically and `data/`, databases, and reports are git-ignored. |
| Runtime supply chain | Runtime is Python standard library only. Development tools are explicit optional dependencies. |

## Accepted risks

- Publisher metadata is taken as supplied. `ntx verify` checks registry metadata
  and content hashes, but a resolving DOI does not prove that an abstract is
  correct or that a retraction notice is absent.
- Cached PMC JATS body text and local gold corpora are third-party content.
  Anyone with access to `data/` or the SQLite database can read them. Treat those
  paths as sensitive and delete them when no longer needed.
- Full-text redistribution is not permitted by the project. The public repository
  ships redacted gold labels only.
- Ledger checkpoints are a local hash chain, not a signed timestamp. An attacker
  who can rewrite the entire checkpoint directory can recompute the chain.
- `urllib` uses the system trust store; there is no certificate pinning.
- The annotation helper is static HTML. Serve it on `127.0.0.1` only and stop the
  server when finished.

## Deployment checklist

1. Run as an unprivileged user and keep `NTX_DATA`, `NTX_BUILD`, and
   `NTX_REPORTS` in a dedicated directory.
2. Keep the API on loopback unless you have a reverse proxy, TLS, and a strong
   token.
3. Set `NTX_API_ALLOW_HOSTS` explicitly when binding beyond loopback.
4. Mount `data/` read-only for report-only workloads.
5. Review `gold/` and `data/` before publishing derived artefacts.
