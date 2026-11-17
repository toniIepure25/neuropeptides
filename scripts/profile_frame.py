"""Descriptive profile of the frozen gold-v2 corpus.

Produces aggregate surveillance statistics (design mix, claim yield, direction
distribution, rubric bands, contradictions) with no accuracy claims: there is no
human gold for this frame yet. Output is deterministic under a frozen clock.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("NTX_ROOT", str(ROOT))

from ntx import clock  # noqa: E402
from ntx.models import Document  # noqa: E402
from ntx.pipeline import process_documents  # noqa: E402
from ntx.store import Store  # noqa: E402


def _document(item: dict) -> Document:
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


def _table(title: str, counts: Counter, total: int) -> list[str]:
    lines = ["### " + title, "", "| Value | Claims | Share |", "| --- | ---: | ---: |"]
    for value, count in counts.most_common():
        share = (100.0 * count / total) if total else 0.0
        lines.append("| %s | %d | %.1f%% |" % (value, count, share))
    lines.append("")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=str(ROOT / "data" / "gold" / "corpus_v2.json"))
    parser.add_argument("--frame", default=str(ROOT / "gold" / "v2" / "sampling_frame.json"))
    parser.add_argument("--out", default=str(ROOT / "docs" / "DESCRIPTIVE_RESULTS.md"))
    parser.add_argument("--json", default=str(ROOT / "build" / "frame_profile.json"))
    parser.add_argument("--db", default=str(ROOT / "build" / "frame_profile.db"))
    args = parser.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    frame = json.loads(Path(args.frame).read_text(encoding="utf-8"))
    docs = [_document(item) for item in corpus["documents"]]
    strata = {item["doc_id"]: item.get("stratum", "unknown") for item in frame["documents"]}

    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()
    store = Store(db_path)
    try:
        stats = process_documents(store, docs, {})
        counts = store.counts()
        provenance = store.provenance_errors()

        directions = Counter(row["direction"] for row in store.conn.execute("SELECT direction FROM claims"))
        outcomes = Counter(row["outcome"] for row in store.conn.execute("SELECT outcome FROM claims"))
        designs = Counter(row["design"] for row in store.conn.execute("SELECT design FROM claims"))
        species = Counter(row["species"] for row in store.conn.execute("SELECT species FROM claims"))
        roles = Counter(row["role"] for row in store.conn.execute("SELECT role FROM claims"))
        sections = Counter(row["section"] for row in store.conn.execute("SELECT section FROM claims"))
        bands = Counter(row["band"] for row in store.conn.execute("SELECT band FROM appraisals"))
        retractions = Counter(
            row["retraction_status"] for row in store.conn.execute("SELECT retraction_status FROM documents")
        )

        claims_per_doc: Counter = Counter()
        claims_by_stratum: Counter = Counter()
        docs_with_claims_by_stratum: Counter = Counter()
        for row in store.conn.execute("SELECT doc_id, COUNT(*) AS n FROM claims GROUP BY doc_id"):
            claims_per_doc[row["n"]] += 1
            stratum = strata.get(row["doc_id"], "unknown")
            claims_by_stratum[stratum] += row["n"]
            docs_with_claims_by_stratum[stratum] += 1

        pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in store.conn.execute(
            "SELECT entity_id, outcome, direction FROM claims WHERE role='intervention'"
        ):
            pairs[(row["entity_id"], row["outcome"])].add(row["direction"])
        contradictions = [
            {"entity_id": entity, "outcome": outcome, "directions": sorted(values)}
            for (entity, outcome), values in pairs.items()
            if {"increase", "decrease"} <= values
        ]
        entities_in_claims = {
            row["entity_id"] for row in store.conn.execute("SELECT DISTINCT entity_id FROM claims")
        }
        entities_in_mentions = {
            row["entity_id"] for row in store.conn.execute("SELECT DISTINCT entity_id FROM mentions")
        }
        retracted_docs = sum(
            1
            for row in store.conn.execute("SELECT retraction_status FROM documents")
            if row["retraction_status"] not in (None, "", "none")
        )
        payload = {
            "generated_at": clock.iso(),
            "corpus": args.corpus,
            "documents": counts["documents"],
            "mentions": counts["mentions"],
            "claims": counts["claims"],
            "appraisals": counts["appraisals"],
            "documents_with_claims": sum(claims_per_doc.values()),
            "provenance_errors": len(provenance),
            "invalid_spans": stats.get("invalid_spans"),
            "directions": dict(directions),
            "outcomes": dict(outcomes),
            "designs": dict(designs),
            "species": dict(species),
            "roles": dict(roles),
            "sections": dict(sections),
            "bands": dict(bands),
            "retraction_status": dict(retractions),
            "retracted_documents": retracted_docs,
            "claims_by_stratum": dict(claims_by_stratum),
            "docs_with_claims_by_stratum": dict(docs_with_claims_by_stratum),
            "contradictions": contradictions,
            "entities_with_claims": sorted(entities_in_claims),
            "entities_with_mentions": sorted(entities_in_mentions),
        }
    finally:
        store.close()

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    strata_rows = []
    for stratum in sorted(set(strata.values())):
        docs_in = sum(1 for value in strata.values() if value == stratum)
        strata_rows.append(
            "| %s | %d | %d | %.2f |"
            % (
                stratum,
                docs_in,
                claims_by_stratum.get(stratum, 0),
                claims_by_stratum.get(stratum, 0) / docs_in if docs_in else 0.0,
            )
        )

    lines = [
        "# Descriptive results: gold-v2 frame",
        "",
        "Generated by `scripts/profile_frame.py` from the frozen sampling frame.",
        "These are **descriptive statistics, not accuracy metrics**: the frame has",
        "model draft annotations only, so no precision/recall/F1 is reported here.",
        "Claims are produced by rules-v2 (clause-aware direction, role, section).",
        "Contradictions count intervention claims only; comparator claims are",
        "stored but excluded from that count.",
        "",
        "| Quantity | Value |",
        "| --- | ---: |",
        "| Documents | %d |" % counts["documents"],
        "| Accepted mentions | %d |" % counts["mentions"],
        "| Claims | %d |" % counts["claims"],
        "| Appraisals | %d |" % counts["appraisals"],
        "| Documents with at least one claim | %d |" % sum(claims_per_doc.values()),
        "| Provenance errors | %d |" % len(provenance),
        "| Retracted/concern documents | %d |" % retracted_docs,
        "| Entities with claims | %d |" % len(entities_in_claims),
        "| Entity-outcome contradictions | %d |" % len(contradictions),
        "",
        "## Claim yield by sampling stratum",
        "",
        "| Stratum | Documents | Claims | Claims/doc |",
        "| --- | ---: | ---: | ---: |",
        *strata_rows,
        "",
    ]
    lines.extend(_table("Claim directions", directions, counts["claims"]))
    lines.extend(_table("Claim outcomes", outcomes, counts["claims"]))
    lines.extend(_table("Entity roles", roles, counts["claims"]))
    lines.extend(_table("Abstract section", sections, counts["claims"]))
    lines.extend(_table("Study designs (claim level)", designs, counts["claims"]))
    lines.extend(_table("Species (claim level)", species, counts["claims"]))
    lines.extend(_table("Appraisal bands", bands, counts["appraisals"]))
    lines.extend(_table("Retraction status", retractions, counts["documents"]))

    lines += ["## Contradictions detected", ""]
    if contradictions:
        lines += ["| Entity | Outcome | Directions |", "| --- | --- | --- |"]
        for item in sorted(contradictions, key=lambda value: (value["entity_id"], value["outcome"])):
            lines.append(
                "| %s | %s | %s |" % (item["entity_id"], item["outcome"], ", ".join(item["directions"]))
            )
    else:
        lines.append("No entity-outcome pair in this frame carries both an increase and a decrease claim.")
    lines += [
        "",
        "## Interpretation limits",
        "",
        "- Claim yield is a property of the extractor, not of the literature; low yield",
        "  in a stratum means the current rules miss those sentences, not that the",
        "  stratum lacks findings.",
        "- Contradiction counts are candidate signals for review; direction labels are",
        "  sentence-level and have not been validated against human annotation.",
        "- No mention recall, claim precision or calibration is measurable here.",
        "",
        "Machine-readable copy: `build/frame_profile.json` (git-ignored).",
        "",
    ]

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(
        "documents: %d | claims: %d | claims/doc: %.2f | contradictions: %d"
        % (
            counts["documents"],
            counts["claims"],
            counts["claims"] / counts["documents"] if counts["documents"] else 0.0,
            len(contradictions),
        )
    )
    print("report: " + args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
