"""Explicit peptide generation from precursors and from an existing candidate.

Generation is deliberately narrow and provenance-preserving:

* cleavage uses a named, parameterised protease rule over a user-supplied
  precursor and records the precursor, enzyme, missed-cleavage setting and span;
* analogs are limited to N/C truncations and a single alanine substitution at one
  position, each linked to its parent candidate.

Nothing here ranks, docks, or predicts activity. Every generated sequence is a
hypothesis source, not a claim that the peptide is novel, stable, exposed or
active.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from . import candidates as candidates_mod
from . import clock
from . import precursors as precursors_mod
from .models import Candidate, CandidateLineage
from .store import Store

ENZYME_RULES: dict[str, dict[str, Any]] = {
    "trypsin": {"residues": "KR", "description": "cleaves after K/R, not before P"},
    "chymotrypsin": {"residues": "FWY", "description": "cleaves after F/W/Y, not before P"},
    "lys-c": {"residues": "K", "description": "cleaves after K, not before P"},
    "arg-c": {"residues": "R", "description": "cleaves after R, not before P"},
    "glu-c": {"residues": "DE", "description": "cleaves after D/E, not before P"},
}
MAX_MISSED_CLEAVAGES = 3
MAX_GENERATED = 2000
MAX_ANALOGS = 1000
MAX_TRUNCATIONS = 50


class GenerationError(ValueError):
    """Invalid generation request or unsafe expansion."""


def cleavage_positions(sequence: str, enzyme: str) -> list[int]:
    """Return 0-based cut positions *after* the scissile residue."""
    try:
        residues = ENZYME_RULES[enzyme]["residues"]
    except KeyError as exc:
        raise GenerationError(
            "unknown enzyme %s; choose one of: %s" % (enzyme, ", ".join(sorted(ENZYME_RULES)))
        ) from exc
    positions = []
    for index, residue in enumerate(sequence[:-1]):
        if residue in residues and sequence[index + 1] != "P":
            positions.append(index + 1)
    return positions


def _bounded_int(name: str, value: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GenerationError("%s must be an integer" % name)
    if not low <= value <= high:
        raise GenerationError("%s must be between %d and %d" % (name, low, high))
    return value


def _template_candidate(
    sequence: str,
    name: str,
    target_entity_id: str,
    provenance: str,
    notes: str,
) -> Candidate:
    return Candidate(
        candidate_id=candidates_mod.candidate_id_for(sequence, [], target_entity_id),
        name=name,
        sequence=sequence,
        length=len(sequence),
        target_entity_id=target_entity_id,
        modifications=[],
        properties=candidates_mod.compute_properties(sequence),
        provenance=provenance,
        notes=notes,
        created_at=clock.iso(),
    )


def _lineage_id(
    candidate_id: str,
    source_kind: str,
    source_id: str,
    operation: str,
    start: int | None,
    end: int | None,
    parameters: dict[str, Any],
) -> str:
    payload = {
        "candidate_id": candidate_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "operation": operation,
        "start": start,
        "end": end,
        "parameters": parameters,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return "L-" + digest[:12]


def _insert_generated(
    store: Store,
    templates: list[Candidate],
    lineage: list[CandidateLineage],
    dry_run: bool,
) -> tuple[int, int]:
    existing = 0
    fresh: list[Candidate] = []
    fresh_lineage: list[CandidateLineage] = []
    for candidate, link in zip(templates, lineage, strict=True):
        row = store.conn.execute(
            "SELECT 1 FROM candidates WHERE candidate_id=?", (candidate.candidate_id,)
        ).fetchone()
        if row is not None:
            existing += 1
            continue
        fresh.append(candidate)
        fresh_lineage.append(link)
    if dry_run:
        return len(fresh), existing
    try:
        for candidate, link in zip(fresh, fresh_lineage, strict=True):
            store.add_candidate(candidate, commit=False)
            store.add_lineage(link, commit=False)
        store.conn.commit()
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        store.conn.rollback()
        raise GenerationError("generation rolled back: %s" % exc) from exc
    return len(fresh), existing


def generate_cleavage(
    store: Store,
    precursor_id: str,
    enzyme: str = "trypsin",
    missed_cleavages: int = 0,
    min_length: int = 2,
    max_length: int = 30,
    max_candidates: int = MAX_GENERATED,
    dry_run: bool = False,
) -> dict[str, Any]:
    precursor = precursors_mod.get_precursor(store, precursor_id)
    if precursor is None:
        raise GenerationError("unknown precursor: %s" % precursor_id)
    if enzyme not in ENZYME_RULES:
        raise GenerationError(
            "unknown enzyme %s; choose one of: %s" % (enzyme, ", ".join(sorted(ENZYME_RULES)))
        )
    missed_cleavages = _bounded_int("missed_cleavages", missed_cleavages, 0, MAX_MISSED_CLEAVAGES)
    min_length = _bounded_int("min_length", min_length, candidates_mod.MIN_LENGTH, candidates_mod.MAX_LENGTH)
    max_length = _bounded_int("max_length", max_length, candidates_mod.MIN_LENGTH, candidates_mod.MAX_LENGTH)
    if min_length > max_length:
        raise GenerationError("min_length must not exceed max_length")
    max_candidates = _bounded_int("max_candidates", max_candidates, 1, MAX_GENERATED)

    sequence = precursor["sequence"]
    cuts = [0] + cleavage_positions(sequence, enzyme) + [len(sequence)]
    spans: list[tuple[int, int]] = []
    for start_index in range(len(cuts) - 1):
        for missed in range(missed_cleavages + 1):
            end_index = start_index + missed + 1
            if end_index >= len(cuts):
                break
            start, end = cuts[start_index], cuts[end_index]
            if min_length <= end - start <= max_length:
                spans.append((start, end))

    unique: dict[str, list[tuple[int, int]]] = {}
    for start, end in spans:
        peptide = sequence[start:end]
        unique.setdefault(peptide, []).append((start, end))
    if len(unique) > max_candidates:
        raise GenerationError(
            "request would generate %d unique peptides (limit %d); tighten the enzyme, "
            "missed-cleavage or length filters" % (len(unique), max_candidates)
        )

    templates: list[Candidate] = []
    lineage: list[CandidateLineage] = []
    for peptide, sites in sorted(unique.items(), key=lambda item: item[1][0]):
        start, end = sites[0]
        name = "%s %d-%d" % (precursor["name"], start + 1, end)
        parameters = {
            "enzyme": enzyme,
            "missed_cleavages": missed_cleavages,
            "occurrences": len(sites),
            "sites": [[site_start, site_end] for site_start, site_end in sites],
        }
        candidate = _template_candidate(
            peptide,
            name,
            "",
            "generated:cleavage",
            "cleaved from %s with %s; missed=%d" % (precursor_id, enzyme, missed_cleavages),
        )
        templates.append(candidate)
        lineage.append(
            CandidateLineage(
                lineage_id=_lineage_id(
                    candidate.candidate_id,
                    "precursor",
                    precursor_id,
                    "cleavage",
                    start,
                    end,
                    parameters,
                ),
                candidate_id=candidate.candidate_id,
                operation="cleavage",
                source_kind="precursor",
                source_id=precursor_id,
                start=start,
                end=end,
                parameters=parameters,
                created_at=clock.iso(),
            )
        )

    generated, existing = _insert_generated(store, templates, lineage, dry_run)
    return {
        "precursor_id": precursor_id,
        "enzyme": enzyme,
        "missed_cleavages": missed_cleavages,
        "min_length": min_length,
        "max_length": max_length,
        "dry_run": dry_run,
        "considered": len(templates),
        "generated": generated,
        "existing": existing,
        "candidates": [candidate.to_dict() for candidate in templates],
        "lineage": [link.to_dict() for link in lineage],
    }


def generate_analogs(
    store: Store,
    candidate_id: str,
    truncations: int = 5,
    alanine_scan: bool = True,
    max_variants: int = MAX_ANALOGS,
    dry_run: bool = False,
) -> dict[str, Any]:
    parent = candidates_mod.get_candidate(store, candidate_id)
    if parent is None:
        raise GenerationError("unknown candidate: %s" % candidate_id)
    truncations = _bounded_int("truncations", truncations, 0, MAX_TRUNCATIONS)
    max_variants = _bounded_int("max_variants", max_variants, 1, MAX_ANALOGS)
    if truncations == 0 and not alanine_scan:
        raise GenerationError("select at least one operation: truncations or alanine_scan")

    sequence = parent["sequence"]
    target = parent.get("target_entity_id") or ""
    effective_truncations = min(truncations, max(0, len(sequence) - candidates_mod.MIN_LENGTH))
    proposed: dict[str, dict[str, Any]] = {}

    for removed in range(1, effective_truncations + 1):
        for suffix, operation, start, end, label, terminus in (
            (sequence[removed:], "truncation_n", removed, len(sequence), "dN%d" % removed, "N"),
            (
                sequence[: len(sequence) - removed],
                "truncation_c",
                0,
                len(sequence) - removed,
                "dC%d" % removed,
                "C",
            ),
        ):
            proposed.setdefault(
                suffix,
                {
                    "sequence": suffix,
                    "name": "%s %s" % (parent["name"], label),
                    "operation": operation,
                    "start": start,
                    "end": end,
                    "parameters": {"removed": removed, "terminus": terminus},
                },
            )

    if alanine_scan:
        for position, residue in enumerate(sequence):
            if residue == "A":
                continue
            analog = sequence[:position] + "A" + sequence[position + 1 :]
            proposed.setdefault(
                analog,
                {
                    "sequence": analog,
                    "name": "%s A%d" % (parent["name"], position + 1),
                    "operation": "substitution_ala",
                    "start": position,
                    "end": position + 1,
                    "parameters": {"position": position + 1, "from": residue, "to": "A"},
                },
            )

    if not proposed:
        raise GenerationError(
            "no variants are possible for %s with these operations (sequence too short "
            "or already all-alanine)" % candidate_id
        )
    if len(proposed) > max_variants:
        raise GenerationError(
            "request would generate %d variants (limit %d); reduce truncations or raise "
            "max_variants explicitly" % (len(proposed), max_variants)
        )

    templates: list[Candidate] = []
    lineage: list[CandidateLineage] = []
    for item in sorted(proposed.values(), key=lambda value: value["sequence"]):
        candidate = _template_candidate(
            item["sequence"],
            item["name"],
            target,
            "generated:analog",
            "derived from %s by %s; modifications not inherited" % (candidate_id, item["operation"]),
        )
        templates.append(candidate)
        lineage.append(
            CandidateLineage(
                lineage_id=_lineage_id(
                    candidate.candidate_id,
                    "candidate",
                    candidate_id,
                    item["operation"],
                    item["start"],
                    item["end"],
                    item["parameters"],
                ),
                candidate_id=candidate.candidate_id,
                operation=item["operation"],
                source_kind="candidate",
                source_id=candidate_id,
                parent_id=candidate_id,
                start=item["start"],
                end=item["end"],
                parameters=item["parameters"],
                created_at=clock.iso(),
            )
        )

    generated, existing = _insert_generated(store, templates, lineage, dry_run)
    return {
        "parent_id": candidate_id,
        "truncations": effective_truncations,
        "alanine_scan": bool(alanine_scan),
        "dry_run": dry_run,
        "considered": len(templates),
        "generated": generated,
        "existing": existing,
        "candidates": [candidate.to_dict() for candidate in templates],
        "lineage": [link.to_dict() for link in lineage],
    }


def lineage_rows(store: Store, candidate_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM candidate_lineage"
    params: tuple[Any, ...] = ()
    if candidate_id is not None:
        sql += " WHERE candidate_id=?"
        params = (candidate_id,)
    sql += " ORDER BY created_at, lineage_id"
    rows = store.conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["parameters"] = json.loads(item.get("parameters") or "{}")
        out.append(item)
    return out


def format_lineage(candidate_id: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no lineage recorded for %s" % candidate_id
    lines = ["lineage for %s" % candidate_id]
    for row in rows:
        span = ""
        if row.get("start_pos") is not None and row.get("end_pos") is not None:
            span = " %d-%d" % (row["start_pos"], row["end_pos"])
        parent = " parent=%s" % row["parent_id"] if row.get("parent_id") else ""
        lines.append(
            "  %s  %s %s%s%s  %s"
            % (
                row["lineage_id"],
                row["source_kind"],
                row["source_id"],
                parent,
                span,
                row["operation"],
            )
        )
        params = row.get("parameters") or {}
        if params:
            lines.append(
                "               parameters: "
                + ", ".join("%s=%s" % (key, params[key]) for key in sorted(params))
            )
    return "\n".join(lines)
