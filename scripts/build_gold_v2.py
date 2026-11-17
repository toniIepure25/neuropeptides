"""Build the stratified gold-v2 sampling frame and model draft annotations.

Retrieval is frozen: the sampling frame records the exact queries, timestamp and
content hashes. Full abstracts are written to ``data/gold/corpus_v2.json``
(git-ignored, rebuildable) so the repository never redistributes third-party text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("NTX_ROOT", str(ROOT))

from ntx import annotation, appraise, clock, config, extract, ingest  # noqa: E402
from ntx.models import Document  # noqa: E402
from ntx.nlp import Lexicon  # noqa: E402

COMPOUNDS = {
    "OXT": '"oxytocin"',
    "NPY": '"neuropeptide Y" OR NPY',
    "SP": '"substance P"',
    "BDNF": 'BDNF OR "brain-derived neurotrophic factor"',
    "PEPTIDE_DRUGS": "Semax OR Selank",
}

STRATA = {
    "human_trial": (
        '(PUB_TYPE:"Randomized Controlled Trial" OR PUB_TYPE:"Meta-Analysis" OR PUB_TYPE:"Systematic Review")'
    ),
    "observational": (
        '(cohort OR "cross-sectional" OR observational) NOT (PUB_TYPE:"Randomized Controlled Trial" '
        'OR PUB_TYPE:"Meta-Analysis")'
    ),
    "preclinical": (
        '(mice OR rats OR "in vitro" OR "cell culture") NOT (PUB_TYPE:"Randomized Controlled Trial" '
        'OR PUB_TYPE:"Meta-Analysis")'
    ),
}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _document_from_item(item: dict) -> Document:
    return Document(
        doc_id=item["doc_id"],
        source="europepmc",
        pmid=item.get("pmid"),
        doi=item.get("doi"),
        title=item["title"],
        abstract=item["abstract"],
        journal=item.get("journal") or "",
        year=item.get("year"),
        pubtypes=item.get("pubtypes") or [],
        license=item.get("license") or "",
        content_hash=item.get("content_hash") or "",
        fetched_at=clock.iso(),
    )


def _draft_for(doc: Document, lexicon: Lexicon) -> dict:
    text = doc.text()
    mentions = lexicon.detect(text)
    offset = len(doc.title) + 2
    claims = extract.extract_claims(text, mentions, offset=offset) if doc.abstract else []
    design = appraise.classify_design(doc)
    return {
        "schema_version": annotation.SCHEMA_VERSION,
        "doc_id": doc.doc_id,
        "annotator": "model-draft-v0.3",
        "status": "draft",
        "mentions": [
            {
                "entity_id": mention.entity_id,
                "start": mention.start,
                "end": mention.end,
                "surface": text[mention.start : mention.end],
                "mention_form": "other",
            }
            for mention in mentions
            if 0 <= mention.start < mention.end <= len(text)
        ],
        "claims": [
            {
                "entity_id": claim.entity_id,
                "start": claim.start,
                "end": claim.end,
                "sentence": claim.sentence,
                "direction": claim.direction,
                "outcome": claim.outcome if claim.outcome in annotation.OUTCOMES else "other",
                "role": claim.role,
                "section": claim.section,
                "evidence_start": claim.evidence_start,
                "evidence_end": claim.evidence_end,
                "resolver": claim.resolver,
                "reasons": list(claim.reasons),
                "design": design if design in annotation.DESIGNS else "unknown",
                "species": claim.species,
                "hedged": bool(claim.hedged),
                "negated": bool(claim.negated),
            }
            for claim in claims
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-stratum", type=int, default=8)
    parser.add_argument("--frame", default=str(ROOT / "gold" / "v2" / "sampling_frame.json"))
    parser.add_argument("--corpus", default=str(ROOT / "data" / "gold" / "corpus_v2.json"))
    parser.add_argument("--draft", default=str(ROOT / "gold" / "v2" / "draft.jsonl"))
    parser.add_argument(
        "--from-corpus",
        action="store_true",
        help="rebuild drafts only, replaying the frozen frame and local corpus without network",
    )
    args = parser.parse_args()

    cache = config.data_dir() / "cache"
    lexicon = Lexicon(config.ontology())

    if args.from_corpus:
        frame_path = Path(args.frame)
        corpus_path = Path(args.corpus)
        if not corpus_path.is_file():
            print(
                "corpus not found: %s (fetch it first or pass --corpus)" % corpus_path,
                file=sys.stderr,
            )
            return 2
        try:
            frame_payload = json.loads(frame_path.read_text(encoding="utf-8"))
            corpus_payload = json.loads(corpus_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print("cannot read frame or corpus: %s" % exc, file=sys.stderr)
            return 2
        by_id = {str(doc["doc_id"]): doc for doc in corpus_payload.get("documents", [])}
        order = [str(doc["doc_id"]) for doc in frame_payload.get("documents", [])]
        missing = [doc_id for doc_id in order if doc_id not in by_id]
        if missing:
            print(
                "corpus is missing %d frame documents (e.g. %s)" % (len(missing), ", ".join(missing[:5])),
                file=sys.stderr,
            )
            return 2
        drafts = [_draft_for(_document_from_item(by_id[doc_id]), lexicon) for doc_id in order]
        annotation.write_records(drafts, Path(args.draft))
        claims = sum(len(draft.get("claims", [])) for draft in drafts)
        roles: dict[str, int] = {}
        for draft in drafts:
            for claim in draft.get("claims", []):
                roles[str(claim["role"])] = roles.get(str(claim["role"]), 0) + 1
        print("draft:  %s (offline replay)" % args.draft)
        print("documents: %d | claims: %d | roles: %s" % (len(drafts), claims, dict(sorted(roles.items()))))
        return 0
    seen: set[str] = set()
    documents: list[dict] = []
    sampling: list[dict] = []
    queries: dict[str, dict] = {}

    for compound, terms in COMPOUNDS.items():
        for stratum, filter_terms in STRATA.items():
            query = "(%s) AND %s" % (terms, filter_terms)
            payload = ingest.search(query, args.per_stratum * 5, cache)
            results = (payload.get("resultList") or {}).get("result", []) or []
            taken = 0
            queries["%s/%s" % (compound, stratum)] = {
                "query": query,
                "hits": payload.get("hitCount"),
                "retrieved": len(results),
            }
            for result in results:
                if taken >= args.per_stratum:
                    break
                doc = ingest.doc_from_epmc(result)
                if not doc.abstract or doc.doc_id in seen:
                    continue
                seen.add(doc.doc_id)
                taken += 1
                documents.append(
                    {
                        "doc_id": doc.doc_id,
                        "pmid": doc.pmid,
                        "doi": doc.doi,
                        "title": doc.title,
                        "abstract": doc.abstract,
                        "year": doc.year,
                        "journal": doc.journal,
                        "pubtypes": doc.pubtypes,
                        "license": doc.license,
                        "content_hash": _hash(doc.title + "\n" + doc.abstract),
                        "stratum": stratum,
                        "compound": compound,
                    }
                )
                sampling.append(
                    {
                        "doc_id": doc.doc_id,
                        "pmid": doc.pmid,
                        "doi": doc.doi,
                        "title": doc.title,
                        "year": doc.year,
                        "journal": doc.journal,
                        "content_hash": _hash(doc.title + "\n" + doc.abstract),
                        "stratum": stratum,
                        "compound": compound,
                    }
                )

    corpus = {
        "meta": {
            "built_at": clock.iso(),
            "source": "Europe PMC REST resultType=core",
            "license_note": "Abstracts are cached locally for annotation and evaluation; "
            "the repository stores only identifiers, metadata and annotation spans.",
        },
        "documents": documents,
    }
    frame = {
        "schema_version": 2,
        "built_at": clock.iso(),
        "compounds": COMPOUNDS,
        "strata": STRATA,
        "queries": queries,
        "documents": sampling,
        "counts": {
            "documents": len(sampling),
            "per_stratum": args.per_stratum,
            "corpus_sha256": _hash(json.dumps(documents, sort_keys=True)),
        },
    }

    corpus_path = Path(args.corpus)
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text(json.dumps(corpus, indent=2, sort_keys=True), encoding="utf-8")

    frame_path = Path(args.frame)
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_text(json.dumps(frame, indent=2, sort_keys=True), encoding="utf-8")

    drafts = [_draft_for(_document_from_item(doc), lexicon) for doc in documents]
    annotation.write_records(drafts, Path(args.draft))

    print(
        "documents: %d | strata: %s"
        % (
            len(documents),
            {key: sum(1 for d in sampling if d["stratum"] == key) for key in STRATA},
        )
    )
    print("frame:  " + str(frame_path))
    print("corpus: " + str(corpus_path) + " (git-ignored)")
    print("draft:  " + str(args.draft))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
