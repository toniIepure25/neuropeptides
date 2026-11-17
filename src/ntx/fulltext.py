"""Open-access full-text support (JATS XML from Europe PMC).

The full-text layer is deliberately separate from the abstract pipeline.  It
stores normalized text and section offsets locally, never redistributes the
source, and keeps every claim tied to the exact text snapshot it came from.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import clock, extract, http, reconcile
from .config import ontology
from .nlp import Lexicon
from .store import Store

ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/%s/fullTextXML"
REPORT_SCHEMA = reconcile.REPORT_SCHEMA
FETCH_SCHEMA = "ntx-fulltext-fetch-v1"
EXTRACT_SCHEMA = "ntx-fulltext-extract-v1"
FULLTEXT_SECTIONS = {"results", "discussion", "conclusions"}

MAX_XML_CHARS = 8_000_000
MAX_FULLTEXT_CHARS = 2_000_000
MAX_FULLTEXT_CLAIMS = 5_000


class FulltextError(ValueError):
    """A full-text document could not be accepted.

    ``status`` is a stable machine-readable reason used by the CLI and report.
    """

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ParsedFulltext:
    """Normalized body text and the character offsets of its sections."""

    title: str
    text: str
    sections: tuple[tuple[int, str], ...]


_SECTION_NAMES = {
    "background": "background",
    "introduction": "introduction",
    "methods": "methods",
    "method": "methods",
    "materials and methods": "methods",
    "materials & methods": "methods",
    "results": "results",
    "result": "results",
    "findings": "results",
    "discussion": "discussion",
    "conclusions": "conclusions",
    "conclusion": "conclusions",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _element_text(element: ET.Element) -> str:
    return _clean_text("".join(element.itertext()))


def _section_name(title: str) -> str | None:
    normalized = _clean_text(title).casefold().rstrip(":")
    return _SECTION_NAMES.get(normalized)


def _direct_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _find_first(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def parse_jats(xml: str) -> ParsedFulltext:
    """Parse JATS XML into normalized body text and section offsets.

    Only the article body is used; the abstract is already stored in
    ``documents``.  DOCTYPE and ENTITY declarations are rejected before Expat
    sees them, and oversized input is refused rather than parsed.
    """
    if not xml.strip():
        raise FulltextError("empty", "empty full-text response")
    if len(xml) > MAX_XML_CHARS:
        raise FulltextError("too_large", "full-text XML exceeds %d characters" % MAX_XML_CHARS)
    lowered = xml.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise FulltextError("unsafe_xml", "DOCTYPE or ENTITY declarations are not allowed")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise FulltextError("invalid_xml", "invalid JATS XML: %s" % exc) from exc

    title_element = _find_first(root, "article-title")
    title = _element_text(title_element) if title_element is not None else ""
    body = _find_first(root, "body")
    if body is None:
        raise FulltextError("empty", "JATS document has no body")

    paragraphs: list[tuple[str, str]] = []

    def walk(element: ET.Element, inherited: str) -> None:
        for child in element:
            tag = _local_name(child.tag)
            if tag == "sec":
                title_element = _direct_child(child, "title")
                mapped = _section_name(_element_text(title_element)) if title_element is not None else None
                walk(child, mapped or inherited)
            elif tag in {"p", "caption"}:
                text = _element_text(child)
                if text:
                    paragraphs.append((inherited, text))
            elif tag in {"table-wrap", "fig"}:
                caption = _direct_child(child, "caption")
                if caption is not None:
                    text = _element_text(caption)
                    if text:
                        paragraphs.append((inherited, text))

    walk(body, "other")
    if not paragraphs:
        raise FulltextError("empty", "JATS body has no paragraphs")

    chunks: list[str] = []
    sections: list[tuple[int, str]] = []
    length = 0
    last_section: str | None = None
    for section, paragraph in paragraphs:
        if chunks:
            length += 2  # the "\n\n" separator
        if section != last_section:
            sections.append((length, section))
            last_section = section
        chunks.append(paragraph)
        length += len(paragraph)
        if length > MAX_FULLTEXT_CHARS:
            raise FulltextError(
                "too_large", "normalized full text exceeds %d characters" % MAX_FULLTEXT_CHARS
            )
    return ParsedFulltext(title=title, text="\n\n".join(chunks), sections=tuple(sections))


def normalize_pmcid(value: str | None) -> str:
    """Return a canonical PMC identifier or an empty string."""
    if not value:
        return ""
    match = re.fullmatch(r"\s*(?i:PMC)(\d+)\s*", value)
    if match is None:
        return ""
    return "PMC" + match.group(1)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _detail(exc: BaseException) -> str:
    return str(exc)[:240]


FETCH_STATUSES = (
    "fetched",
    "already_present",
    "no_pmcid",
    "invalid_pmcid",
    "unavailable",
    "too_large",
    "unsafe_xml",
    "empty",
    "invalid_xml",
    "not_found",
)

EXTRACT_STATUSES = ("documents", "claims", "skipped_existing", "empty", "truncated")


def fetch_fulltext(
    store: Store,
    *,
    doc_id: str | None = None,
    limit: int = 0,
    cache_dir: Path,
    offline: bool = False,
) -> dict[str, Any]:
    """Fetch and store JATS full text for eligible documents.

    Snapshots are immutable in v1: a document that already has a stored text is
    reported as ``already_present`` and is not fetched again.
    """
    counts = dict.fromkeys(FETCH_STATUSES, 0)
    items: list[dict[str, Any]] = []
    docs = sorted(store.documents(), key=lambda row: row["doc_id"])
    if doc_id:
        docs = [row for row in docs if row["doc_id"] == doc_id]
        if not docs:
            counts["not_found"] = 1
            items.append({"doc_id": doc_id, "pmcid": "", "status": "not_found"})
    attempted = 0
    for doc in docs:
        current = doc["doc_id"]
        raw_pmcid = doc["pmcid"] or ""
        pmcid = normalize_pmcid(raw_pmcid)
        if not raw_pmcid:
            counts["no_pmcid"] += 1
            items.append({"doc_id": current, "pmcid": "", "status": "no_pmcid"})
            continue
        if not pmcid:
            counts["invalid_pmcid"] += 1
            items.append({"doc_id": current, "pmcid": raw_pmcid, "status": "invalid_pmcid"})
            continue
        if store.get_document_text(current) is not None:
            counts["already_present"] += 1
            items.append({"doc_id": current, "pmcid": pmcid, "status": "already_present"})
            continue
        if limit and attempted >= limit:
            break
        attempted += 1
        url = ENDPOINT % pmcid
        status = "unavailable"
        detail = ""
        try:
            xml = http.get_text(url, cache_dir, offline=offline)
        except http.OfflineError as exc:
            detail = _detail(exc)
        except ValueError as exc:
            status = "too_large" if "exceeds" in str(exc) else "unavailable"
            detail = _detail(exc)
        except RuntimeError as exc:
            detail = _detail(exc)
        else:
            try:
                parsed = parse_jats(xml)
            except FulltextError as exc:
                status = exc.status
                detail = _detail(exc)
            else:
                store.add_document_text(
                    doc_id=current,
                    pmcid=pmcid,
                    source_url=url,
                    text=parsed.text,
                    sections=[{"name": name, "start": start} for start, name in parsed.sections],
                    content_hash=_sha256(parsed.text),
                    fetched_at=clock.iso(),
                    license=doc["license"] or "",
                )
                counts["fetched"] += 1
                items.append({"doc_id": current, "pmcid": pmcid, "status": "fetched"})
                continue
        counts[status] += 1
        item = {"doc_id": current, "pmcid": pmcid, "status": status}
        if detail:
            item["detail"] = detail
        items.append(item)
    return {
        "schema": FETCH_SCHEMA,
        "generated_at": clock.iso(),
        "counts": counts,
        "items": items,
    }


def extract_fulltext(store: Store, *, doc_id: str | None = None, limit: int = 0) -> dict[str, Any]:
    """Extract section-aware claims from stored full-text snapshots."""
    counts = dict.fromkeys(EXTRACT_STATUSES, 0)
    snapshots = sorted(store.document_texts(), key=lambda row: row["doc_id"])
    if doc_id:
        snapshots = [row for row in snapshots if row["doc_id"] == doc_id]
    if limit:
        snapshots = snapshots[:limit]
    lex = Lexicon(ontology())
    for snapshot in snapshots:
        counts["documents"] += 1
        text = snapshot["text"]
        labels = [(int(item["start"]), str(item["name"])) for item in snapshot["sections"]]
        mentions = lex.detect(text)
        kept = []
        seen: set[tuple[str, int, int]] = set()
        for mention in mentions:
            if mention.start < 0 or mention.end > len(text) or mention.start >= mention.end:
                continue
            key = (mention.entity_id, mention.start, mention.end)
            if key in seen:
                continue
            seen.add(key)
            kept.append(mention)
        claims = extract.extract_claims(text, kept, section_labels=labels)
        claims = [claim for claim in claims if claim.section in FULLTEXT_SECTIONS]
        claims.sort(key=lambda claim: (claim.start, claim.end))
        if len(claims) > MAX_FULLTEXT_CLAIMS:
            counts["truncated"] += len(claims) - MAX_FULLTEXT_CLAIMS
            claims = claims[:MAX_FULLTEXT_CLAIMS]
        if not claims:
            counts["empty"] += 1
            continue
        for claim in claims:
            claim.doc_id = snapshot["doc_id"]
        added = store.add_fulltext_claims(claims, text_hash=snapshot["content_hash"])
        counts["claims"] += added
        counts["skipped_existing"] += len(claims) - added
    return {
        "schema": EXTRACT_SCHEMA,
        "generated_at": clock.iso(),
        "counts": counts,
    }


def format_fetch(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    lines = ["full-text fetch", "  " + ", ".join("%s=%d" % (key, counts[key]) for key in FETCH_STATUSES)]
    problems = [item for item in payload["items"] if item["status"] not in {"fetched", "already_present"}]
    if problems:
        lines.append("  issues:")
        for item in problems[:20]:
            detail = " - " + item["detail"] if item.get("detail") else ""
            lines.append("    %s %s%s" % (item["doc_id"], item["status"], detail))
    return "\n".join(lines)


def format_extract(payload: dict[str, Any]) -> str:
    counts = payload["counts"]
    return "full-text extract\n  " + ", ".join("%s=%d" % (key, counts[key]) for key in EXTRACT_STATUSES)


def build_report(store: Store, *, entity_id: str | None = None) -> dict[str, Any]:
    """Build a descriptive claim-level reconciliation report."""
    return reconcile.build_report(store, entity_id=entity_id)


def format_report(payload: dict[str, Any]) -> str:
    """Format a reconciliation report for the terminal."""
    return reconcile.format_report(payload)


def should_fail(payload: dict[str, Any], threshold: str) -> bool:
    """Return whether any finding meets the requested severity threshold."""
    return reconcile.should_fail(payload, threshold)
