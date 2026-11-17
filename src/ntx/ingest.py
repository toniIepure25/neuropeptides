"""Ingestion adapters for Europe PMC, PubMed E-utilities, and Crossref.

All responses pass through the on-disk cache in ``ntx.http``. Publication
metadata is stored; full text is not redistributed - only short quoted spans
needed for provenance are kept in the database.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from pathlib import Path
from typing import Any

from . import clock, http
from .models import Document

EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EPMC_ANNOT = "https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds"
PUBMED_SUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
CROSSREF_WORK = "https://api.crossref.org/works/"


def _now() -> str:
    return clock.iso()


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def _hash(title: str, abstract: str) -> str:
    return hashlib.sha1((title + "\n" + abstract).encode("utf-8")).hexdigest()


def search(query: str, page_size: int, cache_dir: Path, offline: bool = False) -> dict[str, Any]:
    params = {
        "query": query,
        "format": "json",
        "pageSize": str(page_size),
        "resultType": "core",
    }
    return http.get_json(EPMC_SEARCH + "?" + urllib.parse.urlencode(params), cache_dir, offline)


def doc_from_epmc(result: dict[str, Any], raw_path: str = "", fetched_at: str | None = None) -> Document:
    pmid = str(result.get("pmid") or "") or None
    doi = (result.get("doi") or "") or None
    if pmid:
        doc_id = "PMID:" + pmid
    elif doi:
        doc_id = "DOI:" + doi.lower()
    else:
        doc_id = "EPMC:" + str(result.get("id"))
    title = _clean(result.get("title"))
    abstract = _clean(result.get("abstractText"))
    pubtypes: list[str] = []
    for item in (result.get("pubTypeList") or {}).get("pubType", []) or []:
        if isinstance(item, str):
            pubtypes.append(item)
    raw_year = result.get("pubYear")
    year = None
    try:
        year = int(raw_year) if raw_year else None
    except (TypeError, ValueError):
        year = None
    return Document(
        doc_id=doc_id,
        source="europepmc",
        pmid=pmid,
        pmcid=result.get("pmcid") or None,
        doi=doi,
        title=title,
        abstract=abstract,
        journal=result.get("journalTitle") or "",
        year=year,
        pubtypes=pubtypes,
        license=result.get("license") or "",
        raw_path=raw_path,
        content_hash=_hash(title, abstract),
        fetched_at=fetched_at or _now(),
    )


def pubmed_summaries(pmids: list[str], cache_dir: Path, offline: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    unique = [p for p in dict.fromkeys(pmids) if p]
    for chunk_start in range(0, len(unique), 100):
        chunk = unique[chunk_start : chunk_start + 100]
        params = {"db": "pubmed", "id": ",".join(chunk), "retmode": "json"}
        payload = http.get_json(PUBMED_SUMMARY + "?" + urllib.parse.urlencode(params), cache_dir, offline)
        result = payload.get("result", {})
        for uid in result.get("uids", []):
            out[uid] = result.get(uid)
    return out


def apply_pubmed(doc: Document, summary: dict[str, Any] | None) -> Document:
    if not summary:
        return doc
    pubtypes = [t for t in summary.get("pubtype", []) if isinstance(t, str)]
    if pubtypes:
        merged = list(dict.fromkeys(doc.pubtypes + pubtypes))
        doc.pubtypes = merged
        from .appraise import retraction_from_pubtypes

        status = retraction_from_pubtypes(merged)
        if status:
            doc.retraction_status = status
    if not doc.doi:
        for item in summary.get("articleids", []) or []:
            if item.get("idtype") == "doi":
                doc.doi = item.get("value")
    return doc


def crossref_work(doi: str, cache_dir: Path, offline: bool = False) -> dict[str, Any] | None:
    if not doi:
        return None
    url = CROSSREF_WORK + urllib.parse.quote(doi, safe="")
    try:
        return http.get_json(url, cache_dir, offline)
    except Exception:
        return None


def annotations_for(
    pmids: list[str], cache_dir: Path, offline: bool = False
) -> dict[str, list[dict[str, Any]]]:
    """Europe PMC entity annotations keyed by PMID."""
    out: dict[str, list[dict[str, Any]]] = {}
    unique = [p for p in dict.fromkeys(pmids) if p]
    for chunk_start in range(0, len(unique), 50):
        chunk = unique[chunk_start : chunk_start + 50]
        ids = ",".join("MED:" + p for p in chunk)
        params = {"articleIds": ids, "format": "JSON"}
        try:
            payload = http.get_json(EPMC_ANNOT + "?" + urllib.parse.urlencode(params), cache_dir, offline)
        except Exception:
            continue
        for record in payload or []:
            pmid = str(record.get("extId") or "")
            if pmid:
                out[pmid] = record.get("annotations", []) or []
    return out


def ingest_query(
    query: str,
    limit: int,
    cache_dir: Path,
    offline: bool = False,
    enrich_pubmed: bool = True,
    enrich_crossref: bool = False,
) -> tuple[list[Document], dict[str, list[dict[str, Any]]]]:
    payload = search(query, limit, cache_dir, offline)
    results = (payload.get("resultList") or {}).get("result", []) or []
    docs = [doc_from_epmc(r) for r in results]
    docs = [d for d in docs if d.title]
    pmids = [d.pmid for d in docs if d.pmid]
    summaries = pubmed_summaries(pmids, cache_dir, offline) if enrich_pubmed else {}
    for doc in docs:
        if doc.pmid:
            apply_pubmed(doc, summaries.get(doc.pmid))
    if enrich_crossref:
        from .appraise import merge_crossref_status

        for doc in docs:
            if doc.doi:
                payload_cr = crossref_work(doc.doi, cache_dir, offline)
                doc.retraction_status = merge_crossref_status(doc.retraction_status, payload_cr)
    annotations = annotations_for(pmids, cache_dir, offline) if pmids else {}
    return docs, annotations
