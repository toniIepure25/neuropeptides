# Integrity checks: source verification and ledger checkpoints

This document defines the source-metadata and ledger integrity layer. It adds two
read-only checks:

1. `ntx verify` compares locally stored document metadata with Crossref or PubMed.
2. `ntx ledger checkpoint` and `ntx ledger verify-checkpoints` create and audit a
   hash chain over ledger exports.

Neither feature changes a document, claim, retraction status, prediction or
outcome. Database schema v8 includes the optional full-text and quantitative
effect columns, but this document's `ntx verify` path remains metadata-only;
ledger export schema v3 is unchanged. Full-text span checking is described in
`docs/FULLTEXT.md`; `ntx db backup|verify|restore` and `ntx doctor` cover local
database integrity separately.

## 1. Source verification

```powershell
ntx verify --db build/demo.db
ntx verify --db build/demo.db --doc-id PMID:12345678 --json
ntx verify --db build/demo.db --limit 20 --offline --out reports/verification.json
ntx verify --db build/demo.db --fail-on-drift
```

The check uses the DOI first and falls back to the PMID. Documents with neither a
valid DOI nor a numeric PMID are `unverifiable`, not failures. Requests go through
the existing HTTPS-only, rate-limited, cached `ntx.http` layer; `--offline` reads
only the cache.

### Statuses

| Status | Meaning |
| --- | --- |
| `verified` | The registry record exists, the title/year agree where comparable, and no more severe notice was found. |
| `metadata_mismatch` | The remote title or year differs from the local record. |
| `retraction_drift` | The remote record has a more severe retraction-like status than the local one. |
| `local_retraction` | The local record already carries a retraction/concern/notice; the remote record does not contradict it. |
| `correction` | Crossref reports a correction or erratum for the record. |
| `unverifiable` | No valid DOI/PMID, so no check was attempted. |
| `unavailable` | Offline cache miss, 404, malformed response or network failure. |

Remote statuses are `none`, `notice`, `concern`, `retracted`, `corrected` or
`unknown`. Severity is `none < notice < concern < retracted`; a local retraction is
never downgraded because a registry response is missing a notice.

Title comparison removes HTML/JATS tags, decodes HTML entities, collapses
whitespace and case-folds. Year comparison is exact and only runs when both years
are present. A missing remote title/year produces `null`, not a mismatch.

`--fail-on-drift` returns exit code `1` for `metadata_mismatch` and
`retraction_drift` only. `unverifiable`, `unavailable` and `correction` are
reported but do not fail the command.

### Report schema

`--out` and `--json` use schema `ntx-source-verification-v1`:

```json
{
  "schema": "ntx-source-verification-v1",
  "generated_at": "2026-11-15T00:00:00+00:00",
  "checked": 1,
  "filter_doc_id": null,
  "counts": {
    "verified": 1,
    "metadata_mismatch": 0,
    "retraction_drift": 0,
    "local_retraction": 0,
    "correction": 0,
    "unverifiable": 0,
    "unavailable": 0
  },
  "items": [
    {
      "doc_id": "PMID:12345678",
      "identifier": "10.0000/example",
      "identifier_kind": "doi",
      "local_status": "none",
      "remote_status": "none",
      "status": "verified",
      "title_match": true,
      "year_match": true,
      "remote_title": "Example title",
      "remote_journal": "Example journal",
      "remote_year": 2020,
      "claim_count": 2,
      "detail": ""
    }
  ],
  "warning": "verification is a snapshot of external metadata; ...",
  "content_hash": "..."
}
```

`claim_count` is the number of local claims attached to the document; it is an
impact indicator, not an automatic invalidation. `content_hash` is SHA-256 over the
canonical report excluding `generated_at` and `content_hash`. It is a file-integrity
hash, not a signature.

## 2. Ledger checkpoints

```powershell
ntx ledger checkpoint --db build/demo.db --out reports/checkpoints/0001.json
ntx ledger checkpoint --db build/demo.db `
  --out reports/checkpoints/0002.json `
  --previous reports/checkpoints/0001.json
ntx ledger verify-checkpoints --db build/demo.db --dir reports/checkpoints
```

A checkpoint captures the current ledger export hash:

```json
{
  "schema": "ntx-checkpoint-v1",
  "sequence": 1,
  "previous_hash": null,
  "ledger_hash": "<64 hex>",
  "created_at": "2026-11-15T00:00:00+00:00",
  "content_hash": "<64 hex>"
}
```

- `ledger_hash` is `export_ledger()["content_hash"]`, which excludes `exported_at`.
- `sequence` starts at 1 and increases by one.
- `previous_hash` is the previous checkpoint's `content_hash`; the first is `null`.
- `content_hash` covers the complete checkpoint except itself, including
  `created_at` and `previous_hash`.

`ntx ledger checkpoint` refuses to overwrite an existing file. `--previous` is
validated before the new file is written. `ntx ledger verify-checkpoints` reads
`*.json` from the directory, sorts by `sequence`, and checks:

- schema and hash format;
- each checkpoint's `content_hash`;
- continuous `1..N` sequence;
- each `previous_hash` link;
- the latest `ledger_hash` against the current database ledger.

Exit codes are `0` for a valid chain, `1` for an integrity failure, and `2` for
invalid arguments or unreadable files.

## 3. What this proves

- A source report records what a registry returned at check time and which local
  claims would be affected by a more severe notice.
- A valid checkpoint chain shows that the recorded snapshots are internally
  consistent and that the current ledger has the hash of the latest snapshot.
- Editing a checkpoint, dropping one, reordering them, or changing the ledger after
  the last checkpoint is detected.

## 4. What this does not prove

- **No trusted time.** `created_at` is local wall-clock time. A checkpoint does not
  prove that a prediction or snapshot existed before a particular external event.
- **No signature.** Anyone who can rewrite the whole directory can recompute every
  hash. The chain detects accidental edits and partial tampering only if an earlier
  hash is anchored somewhere else (for example, a dated archive or a signed
  release artefact).
- **No publisher full-text verification.** `ntx verify` checks registry metadata,
  not that a DOI resolves to the quoted abstract, and not whether the reported
  effect is correct. `ntx fulltext` checks a locally cached PMC JATS body against
  claim spans; it does not verify the publisher's PDF or the scientific claim.
- **No automatic remediation.** A retraction drift report does not delete evidence,
  change a claim or lower a score automatically; it requires human review.
- **No proof of validity.** A missing retraction notice is not evidence that a paper
  is valid or that a candidate works.

Safe statement: "The local ledger is unchanged since checkpoint N, and the source
metadata was checked against Crossref/PubMed on this date."

Unsafe statement: "The prediction was registered before the experiment", "the
source is valid", or "the checkpoint is a trusted timestamp".
