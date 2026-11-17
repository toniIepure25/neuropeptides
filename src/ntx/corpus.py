"""Deterministic corpus loading for evaluation and ablation runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Document


def load_corpus(path: Path) -> tuple[list[Document], dict[str, list[dict[str, Any]]]]:
    """Load a corpus JSON with either ``documents`` or Europe PMC ``results``.

    Returns documents plus any Europe PMC annotation payload keyed by PMID.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = payload.get("documents") or payload.get("results") or []
    docs: list[Document] = []
    for item in raw:
        if "abstract" in item:
            docs.append(
                Document(
                    doc_id=item["doc_id"],
                    pmid=item.get("pmid"),
                    doi=item.get("doi"),
                    title=item.get("title") or "",
                    abstract=item.get("abstract") or "",
                    journal=item.get("journal") or "",
                    year=item.get("year"),
                    pubtypes=item.get("pubtypes") or [],
                    license=item.get("license") or "",
                    content_hash=item.get("content_hash") or "",
                )
            )
        else:
            from .ingest import doc_from_epmc

            docs.append(doc_from_epmc(item))
    return [doc for doc in docs if doc.title], payload.get("annotations", {})
