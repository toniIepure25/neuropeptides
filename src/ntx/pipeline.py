"""End-to-end pipeline: documents -> mentions -> claims -> appraisals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__, appraise, clock, config, extract, ingest
from .config import ontology
from .log import get as get_logger
from .models import Document, Mention
from .nlp import Lexicon
from .store import Store

LOG = get_logger(__name__)


def _now() -> str:
    return clock.iso()


def build_lexicon() -> Lexicon:
    return Lexicon(ontology())


def _valid_span(text: str, mention: Mention) -> bool:
    return (
        0 <= mention.start < mention.end <= len(text) and text[mention.start : mention.end] == mention.surface
    )


def process_documents(
    store: Store,
    docs: Iterable[Document],
    annotations: dict[str, list[dict[str, Any]]] | None = None,
    use_annotations: bool = True,
    flags: extract.FeatureFlags | None = None,
) -> dict[str, int]:
    lex = build_lexicon()
    for entity in lex.entities.values():
        store.add_entity(entity, commit=False)

    flags = flags or extract.FeatureFlags(use_annotations=use_annotations)
    annotations = annotations or {}
    stats = {"documents": 0, "mentions": 0, "claims": 0, "appraisals": 0, "invalid_spans": 0}
    for doc in docs:
        store.add_document(doc, commit=False)
        stats["documents"] += 1
        text = doc.text()
        mentions: list[Mention] = lex.detect(text)
        if flags.use_annotations and doc.pmid and doc.pmid in annotations:
            mentions.extend(lex.mentions_from_annotations(text, annotations[doc.pmid]))

        kept: list[Mention] = []
        seen = set()
        for mention in mentions:
            mention.doc_id = doc.doc_id
            if not _valid_span(text, mention):
                stats["invalid_spans"] += 1
                continue
            key = (mention.entity_id, mention.start, mention.end)
            if key in seen:
                continue
            seen.add(key)
            kept.append(mention)
        store.add_mentions(kept, commit=False)
        stats["mentions"] += len(kept)

        claims = []
        if doc.abstract:
            claims = extract.extract_claims(text, kept, offset=len(doc.title) + 2, flags=flags)
        for claim in claims:
            claim.doc_id = doc.doc_id
        store.add_claims(claims, commit=False)
        stats["claims"] += len(claims)

        appraisal_rows = appraise.appraise_claims(doc, claims)
        store.add_appraisals(appraisal_rows, commit=False)
        stats["appraisals"] += len(appraisal_rows)

    store.conn.commit()
    LOG.info("processed documents", extra={"documents": stats["documents"]})
    return stats


def run_ingest(
    store: Store,
    query: str,
    limit: int,
    cache_dir: Path,
    offline: bool = False,
    enrich_crossref: bool = False,
    flags: extract.FeatureFlags | None = None,
) -> dict[str, int]:
    docs, annotations = ingest.ingest_query(
        query,
        limit,
        cache_dir,
        offline=offline,
        enrich_pubmed=True,
        enrich_crossref=enrich_crossref,
    )
    return process_documents(store, docs, annotations, flags=flags)


@dataclass
class DemoCorpus:
    results: list[dict[str, Any]]
    annotations: dict[str, list[dict[str, Any]]]
    meta: dict[str, Any]


def load_demo_corpus(path: Path | None) -> DemoCorpus:
    """Load a corpus file, or the corpus bundled with the package when path is None."""
    if path is None:
        payload = config.resource_json("demo_corpus.json")
    else:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    return DemoCorpus(
        results=payload.get("results", []),
        annotations=payload.get("annotations", {}),
        meta=payload.get("meta", {}),
    )


def run_demo(
    corpus_path: Path | None,
    db_path: Path,
    manifest: Any | None = None,
    flags: extract.FeatureFlags | None = None,
) -> dict[str, int]:
    corpus = load_demo_corpus(corpus_path)
    fetched_at = corpus.meta.get("built_at") or None
    docs = [ingest.doc_from_epmc(result, fetched_at=fetched_at) for result in corpus.results]
    docs = [doc for doc in docs if doc.title]
    if db_path.exists():
        db_path.unlink()
    store = Store(db_path)
    try:
        stats = process_documents(store, docs, corpus.annotations, flags=flags)
        if manifest is not None:
            store.add_manifest(manifest)
        return stats
    finally:
        store.close()


def record_run(store: Store, config_fingerprint: str, notes: str = "") -> str:
    run_id = hashlib.sha1((config_fingerprint + _now()).encode("utf-8")).hexdigest()[:12]
    store.conn.execute(
        "INSERT INTO runs (run_id, started, finished, config_hash, code_version, notes) VALUES (?,?,?,?,?,?)",
        (run_id, _now(), _now(), config_fingerprint, __version__, notes),
    )
    store.conn.commit()
    return run_id
