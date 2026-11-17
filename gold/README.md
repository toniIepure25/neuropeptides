# Gold annotations

The public repository ships **text-free annotations**: labels, exact character
spans, document identifiers, and SHA-256 hashes of the removed sentence/surface.
It does **not** redistribute third-party abstract or full-text sentences.

| File | Contents | Public? |
| --- | --- | --- |
| `pilot.jsonl` | 6-document pilot, redacted | yes |
| `v2/draft.jsonl` | 119-document model-assisted draft, redacted | yes |
| `v2/sampling_frame.json` | bibliographic sampling frame (titles, identifiers, hashes) | yes |
| `PROTOCOL_V2.md`, `GUIDELINES.md` | annotation protocol and codebook | yes |
| `data/private_gold/pilot_annotations.json` | builder input with verbatim snippets | local only |
| `data/gold/corpus_v2.json` | frozen full-text corpus | local only, git-ignored |

## Why hashes instead of sentences

The annotation labels are useful for inspection and agreement work, but the
underlying text belongs to the publisher. A hash proves that the public label
matches the local sentence without publishing the sentence.

A redacted claim looks like:

```json
{
  "entity_id": "NP:OXT",
  "start": 911,
  "end": 1060,
  "sentence_sha256": "063f6f82bc2053d5d456ac2cd8abb9311d3fbb20e38cc7d674035e3d13dccfff",
  "direction": "increase",
  "outcome": "cognition"
}
```

## Restoring text locally

1. Obtain a corpus JSON with `documents[].doc_id`, `title`, and `abstract` from a
   source you are allowed to use.
2. Hydrate into a git-ignored path:

```powershell
python -m ntx gold hydrate `
  --in gold/v2/draft.jsonl `
  --corpus data/gold/corpus_v2.json `
  --out build/gold.full.jsonl
```

3. Evaluate or validate against the hydrated file:

```powershell
python -m ntx gold validate --file build/gold.full.jsonl --corpus data/gold/corpus_v2.json
python -m ntx eval --db build/ntx.db --gold build/gold.full.jsonl
```

`ntx eval` refuses redacted gold by design. Hydration verifies every hash and
fails if a span is outside the corpus or the sentence has changed.

## Rebuilding the redacted draft

```powershell
python scripts/build_gold_v2.py --from-corpus --draft build/draft.full.jsonl
python -m ntx gold redact --in build/draft.full.jsonl --out gold/v2/draft.jsonl --force
```

The offline replay test is skipped when `data/gold/corpus_v2.json` is absent.

## Status

There is **no human-validated gold set** in this repository yet. `v2/draft.jsonl`
is model-assisted and must not be reported as human accuracy. See
[`docs/METHOD.md`](../docs/METHOD.md) and [`docs/DATA_GOVERNANCE.md`](../docs/DATA_GOVERNANCE.md).
