"""Candidate peptide validation and transparent heuristic triage.

A candidate is user-supplied or imported. This module never invents a sequence
and never predicts activity: it validates the molecule, computes reproducible
physicochemical descriptors from residue tables, and records the input with a
content-derived id so a later experiment can point back to exactly one molecule.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import clock, config
from .models import Candidate
from .store import Store

CANONICAL = set("ACDEFGHIKLMNPQRSTVWY")
MIN_LENGTH = 2
MAX_LENGTH = 200

# Monoisotopic residue masses, Da.
RESIDUE_MASS = {
    "A": 71.037113805,
    "C": 103.009184505,
    "D": 115.026943065,
    "E": 129.042593135,
    "F": 147.068413945,
    "G": 57.021463735,
    "H": 137.058911875,
    "I": 113.084064015,
    "K": 128.094963050,
    "L": 113.084064015,
    "M": 131.040484645,
    "N": 114.042927470,
    "P": 97.052763875,
    "Q": 128.058577540,
    "R": 156.101111050,
    "S": 87.032028435,
    "T": 101.047678505,
    "V": 99.068413945,
    "W": 186.079312980,
    "Y": 163.063328575,
}
WATER = 18.010564684
KYTE_DOOLITTLE = {
    "A": 1.8,
    "C": 2.5,
    "D": -3.5,
    "E": -3.5,
    "F": 2.8,
    "G": -0.4,
    "H": -3.2,
    "I": 4.5,
    "K": -3.9,
    "L": 3.8,
    "M": 1.9,
    "N": -3.5,
    "P": -1.6,
    "Q": -3.5,
    "R": -4.5,
    "S": -0.8,
    "T": -0.7,
    "V": 4.2,
    "W": -0.9,
    "Y": -1.3,
}
PKA = {
    "N_term": 9.69,
    "C_term": 2.34,
    "D": 3.65,
    "E": 4.25,
    "C": 8.18,
    "Y": 10.07,
    "H": 6.00,
    "K": 10.50,
    "R": 12.40,
}


class CandidateError(ValueError):
    """Invalid candidate input; the message names the field or input line."""


def validate_sequence(raw: str) -> str:
    if not isinstance(raw, str):
        raise CandidateError("sequence must be a string")
    sequence = raw.strip().upper()
    if not sequence:
        raise CandidateError("sequence is empty")
    if any(char not in CANONICAL for char in sequence):
        bad = sorted({char for char in sequence if char not in CANONICAL})
        raise CandidateError("sequence contains non-canonical residue(s): %s" % ", ".join(bad))
    if not MIN_LENGTH <= len(sequence) <= MAX_LENGTH:
        raise CandidateError("sequence length must be between %d and %d" % (MIN_LENGTH, MAX_LENGTH))
    return sequence


def _term_charge(pka: float, ph: float, positive: bool) -> float:
    if positive:
        return 1.0 / (1.0 + 10.0 ** (ph - pka))
    return -1.0 / (1.0 + 10.0 ** (pka - ph))


def net_charge(sequence: str, ph: float = 7.4) -> float:
    charge = _term_charge(PKA["N_term"], ph, True) + _term_charge(PKA["C_term"], ph, False)
    for residue in sequence:
        if residue in {"K", "R", "H"}:
            charge += _term_charge(PKA[residue], ph, True)
        elif residue in {"D", "E", "C", "Y"}:
            charge += _term_charge(PKA[residue], ph, False)
    return charge


def compute_properties(sequence: str) -> dict[str, Any]:
    sequence = validate_sequence(sequence)
    molecular_weight = sum(RESIDUE_MASS[residue] for residue in sequence) + WATER
    gravy = sum(KYTE_DOOLITTLE[residue] for residue in sequence) / len(sequence)
    return {
        "length": len(sequence),
        "molecular_weight": round(molecular_weight, 4),
        "net_charge_pH7_4": round(net_charge(sequence), 3),
        "gravy": round(gravy, 3),
        "aromatic_count": sum(residue in "FWY" for residue in sequence),
        "cysteine_count": sequence.count("C"),
        "protease_motifs": {
            "dpp4_like": len(sequence) >= 2 and sequence[1] in "PA",
            "trypsin_like_sites": sum(residue in "KR" for residue in sequence),
            "chymotrypsin_like_sites": sum(residue in "FWY" for residue in sequence),
        },
        "heuristic": True,
        "modifications_applied_to_properties": False,
        "property_method": "residue-table-v1",
    }


def candidate_id_for(sequence: str, modifications: list[str], target_entity_id: str) -> str:
    payload = {
        "sequence": sequence,
        "modifications": sorted(modifications),
        "target_entity_id": target_entity_id,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return "C-" + digest[:12]


def _entity_ids() -> set[str]:
    return {entity["id"] for entity in config.ontology().get("entities", [])}


def _load_records(path: Path) -> list[tuple[int, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise CandidateError("cannot read %s: %s" % (path, exc)) from exc
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise CandidateError("%s: invalid JSON: %s" % (path, exc)) from exc
        if not isinstance(payload, list):
            raise CandidateError("%s: JSON input must be an array of candidate objects" % path)
        return [(index + 1, item) for index, item in enumerate(payload)]
    records: list[tuple[int, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append((line_number, json.loads(line)))
        except ValueError as exc:
            raise CandidateError("%s: line %d: invalid JSON: %s" % (path, line_number, exc)) from exc
    return records


def _build(record: Any, line_number: int) -> Candidate:
    prefix = "line %d: " % line_number
    if not isinstance(record, dict):
        raise CandidateError(prefix + "candidate must be an object")
    name = record.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CandidateError(prefix + "name must be a non-empty string")
    if "sequence" not in record:
        raise CandidateError(prefix + "sequence is required")
    try:
        sequence = validate_sequence(record["sequence"])
    except CandidateError as exc:
        raise CandidateError(prefix + str(exc)) from exc

    modifications = record.get("modifications", [])
    if not isinstance(modifications, list) or any(not isinstance(item, str) for item in modifications):
        raise CandidateError(prefix + "modifications must be a list of strings")
    target = record.get("target_entity_id", "")
    if not isinstance(target, str):
        raise CandidateError(prefix + "target_entity_id must be a string")
    if target and target not in _entity_ids():
        raise CandidateError(prefix + "unknown target_entity_id: %s" % target)
    hypothesis_id = record.get("hypothesis_id", "")
    if not isinstance(hypothesis_id, str):
        raise CandidateError(prefix + "hypothesis_id must be a string")
    provenance = record.get("provenance", "user-supplied")
    if not isinstance(provenance, str) or not provenance:
        raise CandidateError(prefix + "provenance must be a non-empty string")
    notes = record.get("notes", "")
    if not isinstance(notes, str):
        raise CandidateError(prefix + "notes must be a string")

    candidate_id = candidate_id_for(sequence, modifications, target)
    return Candidate(
        candidate_id=candidate_id,
        name=name.strip(),
        sequence=sequence,
        length=len(sequence),
        target_entity_id=target,
        hypothesis_id=hypothesis_id,
        modifications=modifications,
        properties=compute_properties(sequence),
        provenance=provenance,
        notes=notes,
        created_at=clock.iso(),
    )


def import_candidates(store: Store, path: Path, dry_run: bool = False) -> dict[str, Any]:
    records = _load_records(Path(path))
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for line_number, record in records:
        candidate = _build(record, line_number)
        if candidate.candidate_id in seen:
            raise CandidateError("line %d: duplicate candidate id %s" % (line_number, candidate.candidate_id))
        exists = store.conn.execute(
            "SELECT 1 FROM candidates WHERE candidate_id=?", (candidate.candidate_id,)
        ).fetchone()
        if exists:
            raise CandidateError(
                "line %d: candidate %s already exists; ledger rows are append-only"
                % (line_number, candidate.candidate_id)
            )
        seen.add(candidate.candidate_id)
        candidates.append(candidate)

    if not dry_run:
        try:
            for candidate in candidates:
                store.add_candidate(candidate, commit=False)
            store.conn.commit()
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
            store.conn.rollback()
            raise CandidateError("candidate import rolled back: %s" % exc) from exc
    return {
        "imported": len(candidates),
        "dry_run": dry_run,
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def candidate_rows(store: Store) -> list[dict[str, Any]]:
    rows = store.conn.execute("SELECT * FROM candidates ORDER BY candidate_id").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["modifications"] = json.loads(item.get("modifications") or "[]")
        item["properties"] = json.loads(item.get("properties") or "{}")
        out.append(item)
    return out


def get_candidate(store: Store, candidate_id: str) -> dict[str, Any] | None:
    row = store.conn.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["modifications"] = json.loads(item.get("modifications") or "[]")
    item["properties"] = json.loads(item.get("properties") or "{}")
    return item


def format_candidates(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "no candidates in this database"
    lines = ["%-16s %-22s %5s %-10s %8s %7s" % ("candidate", "name", "len", "target", "MW", "charge")]
    for candidate in candidates:
        props = candidate.get("properties", {})
        lines.append(
            "%-16s %-22s %5d %-10s %8.2f %7.2f"
            % (
                candidate["candidate_id"],
                candidate["name"][:22],
                candidate["length"],
                candidate.get("target_entity_id") or "-",
                props.get("molecular_weight", 0.0),
                props.get("net_charge_pH7_4", 0.0),
            )
        )
    return "\n".join(lines)


def format_candidate(candidate: dict[str, Any]) -> str:
    props = candidate.get("properties", {})
    motifs = props.get("protease_motifs", {})
    lines = [
        "%s  %s" % (candidate["candidate_id"], candidate["name"]),
        "sequence:      %s" % candidate["sequence"],
        "target:        %s" % (candidate.get("target_entity_id") or "-"),
        "provenance:    %s" % candidate.get("provenance", ""),
        "modifications: %s" % (", ".join(candidate.get("modifications", [])) or "-"),
        "MW:            %.4f Da" % props.get("molecular_weight", 0.0),
        "charge pH 7.4: %.3f" % props.get("net_charge_pH7_4", 0.0),
        "GRAVY:         %.3f" % props.get("gravy", 0.0),
        "aromatic:      %d" % props.get("aromatic_count", 0),
        "cysteines:     %d" % props.get("cysteine_count", 0),
        "protease motifs: DPP4-like=%s trypsin-like=%d chymotrypsin-like=%d"
        % (
            motifs.get("dpp4_like", False),
            motifs.get("trypsin_like_sites", 0),
            motifs.get("chymotrypsin_like_sites", 0),
        ),
        "note: heuristic residue-table descriptors; not activity or permeability predictions",
    ]
    return "\n".join(lines)
