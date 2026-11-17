"""Verify local document metadata against Crossref and PubMed.

This module is read-only with respect to the scientific record. A verification
report never changes a document, claim or retraction status; it only records what
an external registry returned at check time. Absence of a retraction notice is not
proof that a paper is valid.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from . import clock, http
from .models import Document
from .store import Store

SCHEMA = "ntx-source-verification-v1"
CROSSREF_WORK = "https://api.crossref.org/works/"
PUBMED_SUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
MAX_LIMIT = 10000
STATUS_ORDER = {"none": 0, "notice": 1, "concern": 2, "retracted": 3}
STATUSES = (
    "verified",
    "metadata_mismatch",
    "retraction_drift",
    "local_retraction",
    "correction",
    "unverifiable",
    "unavailable",
)
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_PMID = re.compile(r"^[0-9]{1,9}$")


class VerificationError(ValueError):
    """Invalid verification input or report parameters."""


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    lowered = text.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if lowered.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return text if _DOI.match(text) else None


def normalize_pmid(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if _PMID.match(text) else None


def _normalized_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _year(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        match = re.search(r"\b(1[89][0-9]{2}|20[0-9]{2})\b", value)
        if match:
            return int(match.group(1))
    return None


def _first_list(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value or "")


def _remote_status_from_crossref(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        return "unknown"
    status = "none"
    for update in message.get("update-to") or []:
        if not isinstance(update, dict):
            continue
        kind = str(update.get("type") or "").lower()
        if kind == "retraction":
            return "retracted"
        if kind == "expression-of-concern":
            status = "concern"
        elif kind in {"correction", "erratum"} and status == "none":
            status = "corrected"
    return status


def _crossref_record(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        raise VerificationError("malformed Crossref response")
    year = None
    issued = message.get("issued") or {}
    if isinstance(issued, dict):
        parts = issued.get("date-parts") or []
        if parts and isinstance(parts[0], list) and parts[0]:
            year = _year(parts[0][0])
    return {
        "title": _first_list(message.get("title")),
        "journal": _first_list(message.get("container-title")),
        "year": year,
        "status": _remote_status_from_crossref(payload),
    }


def _pubmed_record(payload: dict[str, Any], pmid: str) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise VerificationError("malformed PubMed response")
    summary = result.get(pmid)
    if not isinstance(summary, dict):
        raise VerificationError("PubMed record not found: %s" % pmid)
    raw_pubtypes = summary.get("pubtype", []) or []
    if isinstance(raw_pubtypes, str):
        raw_pubtypes = [raw_pubtypes]
    pubtypes = [item for item in raw_pubtypes if isinstance(item, str)]
    joined = " | ".join(item.lower() for item in pubtypes)
    if "retracted publication" in joined:
        status = "retracted"
    elif "expression of concern" in joined:
        status = "concern"
    elif "retraction of publication" in joined:
        status = "notice"
    else:
        status = "none"
    return {
        "title": str(summary.get("title") or ""),
        "journal": str(summary.get("fulljournalname") or summary.get("source") or ""),
        "year": _year(summary.get("pubdate") or summary.get("epubdate")),
        "status": status,
    }


def _fetch(url: str, cache_dir: Path, offline: bool) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = http.get_json(url, cache_dir, offline, retries=1)
    except http.OfflineError as exc:
        return None, "offline cache miss: %s" % exc
    except Exception as exc:  # network, HTTP status, malformed cache
        return None, "external request failed: %s" % exc
    if not isinstance(payload, dict):
        return None, "external response was not an object"
    return payload, None


def _identifier(doc: Document) -> tuple[str | None, str | None]:
    doi = normalize_doi(doc.doi)
    if doi:
        return "doi", doi
    pmid = normalize_pmid(doc.pmid)
    if pmid:
        return "pmid", pmid
    return None, None


def _local_status(value: str) -> str:
    return "notice" if value == "retraction_notice" else value or "unknown"


def _classify(
    local_status: str,
    remote_status: str,
    title_match: bool | None,
    year_match: bool | None,
) -> str:
    if remote_status == "unavailable":
        return "unavailable"
    local_severity = STATUS_ORDER.get(local_status, 0)
    remote_severity = STATUS_ORDER.get(remote_status, 0)
    if remote_severity > local_severity:
        return "retraction_drift"
    if title_match is False or year_match is False:
        return "metadata_mismatch"
    if local_severity > 0 and remote_severity <= local_severity:
        return "local_retraction"
    if remote_status == "corrected":
        return "correction"
    return "verified"


def _claim_count(store: Store, doc_id: str) -> int:
    row = store.conn.execute("SELECT COUNT(*) AS n FROM claims WHERE doc_id=?", (doc_id,)).fetchone()
    return int(row["n"]) if row is not None else 0


def _verify_one(doc: Document, cache_dir: Path, offline: bool, store: Store) -> dict[str, Any]:
    kind, identifier = _identifier(doc)
    item: dict[str, Any] = {
        "doc_id": doc.doc_id,
        "identifier": identifier,
        "identifier_kind": kind,
        "local_status": _local_status(doc.retraction_status),
        "remote_status": "unknown",
        "status": "unverifiable",
        "title_match": None,
        "year_match": None,
        "remote_title": None,
        "remote_journal": None,
        "remote_year": None,
        "claim_count": _claim_count(store, doc.doc_id),
        "detail": "",
    }
    if kind is None or identifier is None:
        item["detail"] = "no DOI or PMID"
        return item

    if kind == "doi":
        url = CROSSREF_WORK + urllib.parse.quote(identifier, safe="/")
        payload, error = _fetch(url, cache_dir, offline)
        if payload is None:
            item["status"] = "unavailable"
            item["detail"] = error or "no Crossref record"
            return item
        try:
            remote = _crossref_record(payload)
        except VerificationError as exc:
            item["status"] = "unavailable"
            item["detail"] = str(exc)
            return item
    else:
        url = (
            PUBMED_SUMMARY
            + "?"
            + urllib.parse.urlencode({"db": "pubmed", "id": identifier, "retmode": "json"})
        )
        payload, error = _fetch(url, cache_dir, offline)
        if payload is None:
            item["status"] = "unavailable"
            item["detail"] = error or "no PubMed record"
            return item
        try:
            remote = _pubmed_record(payload, identifier)
        except VerificationError as exc:
            item["status"] = "unavailable"
            item["detail"] = str(exc)
            return item

    remote_title = str(remote["title"] or "")
    title_match: bool | None = None
    if doc.title and remote_title:
        title_match = _normalized_title(doc.title) == _normalized_title(remote_title)
    year_match: bool | None = None
    if doc.year is not None and remote["year"] is not None:
        year_match = doc.year == remote["year"]
    item.update(
        {
            "remote_status": remote["status"],
            "status": _classify(
                _local_status(doc.retraction_status), remote["status"], title_match, year_match
            ),
            "title_match": title_match,
            "year_match": year_match,
            "remote_title": remote_title or None,
            "remote_journal": str(remote["journal"] or "") or None,
            "remote_year": remote["year"],
        }
    )
    return item


def _content_hash(payload: dict[str, Any]) -> str:
    clone = dict(payload)
    clone.pop("generated_at", None)
    clone.pop("content_hash", None)
    canonical = json.dumps(clone, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_documents(
    store: Store,
    *,
    doc_id: str | None = None,
    limit: int = 0,
    cache_dir: Path,
    offline: bool = False,
) -> dict[str, Any]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= MAX_LIMIT:
        raise VerificationError("limit must be an integer in [0, %d]" % MAX_LIMIT)
    cache_dir = Path(cache_dir)
    if doc_id:
        doc = store.get_document(doc_id)
        if doc is None:
            raise VerificationError("document not found: %s" % doc_id)
        docs = [doc]
    else:
        rows = sorted(store.documents(), key=lambda row: str(row["doc_id"]))
        docs = []
        for row in rows:
            doc = store.get_document(str(row["doc_id"]))
            if doc is not None:
                docs.append(doc)
    if limit:
        docs = docs[:limit]

    items = [_verify_one(doc, cache_dir, offline, store) for doc in docs]
    counts = dict.fromkeys(STATUSES, 0)
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    warning = (
        "verification is a snapshot of external metadata; absence of a retraction "
        "notice is not proof of validity"
    )
    if counts["unavailable"]:
        warning += "; %d source(s) could not be checked" % counts["unavailable"]
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": clock.iso(),
        "checked": len(items),
        "filter_doc_id": doc_id,
        "counts": counts,
        "items": items,
        "warning": warning,
    }
    payload["content_hash"] = _content_hash(payload)
    return payload


def format_verification(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    lines = [
        "Source verification (Crossref/PubMed)",
        "=====================================",
        "",
        "checked: %d  verified: %d  mismatch: %d  retraction drift: %d"
        % (
            payload["checked"],
            counts.get("verified", 0),
            counts.get("metadata_mismatch", 0),
            counts.get("retraction_drift", 0),
        ),
        "local retraction: %d  correction: %d  unverifiable: %d  unavailable: %d"
        % (
            counts.get("local_retraction", 0),
            counts.get("correction", 0),
            counts.get("unverifiable", 0),
            counts.get("unavailable", 0),
        ),
    ]
    if payload["items"]:
        lines.append("")
        lines.append("doc_id                  status              identifier")
        for item in payload["items"]:
            identifier = item["identifier"] or "-"
            lines.append("%-22s  %-18s  %s" % (item["doc_id"], item["status"], identifier))
    lines.append("")
    lines.append("warning: " + payload["warning"])
    return "\n".join(lines)
