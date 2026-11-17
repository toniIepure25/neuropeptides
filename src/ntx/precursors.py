"""Precursor protein records used as explicit sources for peptide generation.

A precursor is a protein sequence supplied by the user, optionally linked to a
UniProt accession. The system does not fetch sequences automatically and does
not infer a signal peptide or a biologically relevant cleavage pattern: cleavage
is a user-selected, parameterised operation recorded in ``candidate_lineage``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import clock
from .candidates import CANONICAL
from .models import Precursor
from .store import Store

MIN_PRECURSOR_LENGTH = 2
MAX_PRECURSOR_LENGTH = 10000


class PrecursorError(ValueError):
    """Invalid precursor input; the message names the field or input line."""


def validate_precursor_sequence(raw: str) -> str:
    if not isinstance(raw, str):
        raise PrecursorError("sequence must be a string")
    sequence = raw.strip().upper()
    if not sequence:
        raise PrecursorError("sequence is empty")
    if any(char not in CANONICAL for char in sequence):
        bad = sorted({char for char in sequence if char not in CANONICAL})
        raise PrecursorError("sequence contains non-canonical residue(s): %s" % ", ".join(bad))
    if not MIN_PRECURSOR_LENGTH <= len(sequence) <= MAX_PRECURSOR_LENGTH:
        raise PrecursorError(
            "precursor sequence length must be between %d and %d"
            % (MIN_PRECURSOR_LENGTH, MAX_PRECURSOR_LENGTH)
        )
    return sequence


def precursor_id_for(sequence: str) -> str:
    digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    return "PR-" + digest[:12]


def _load_records(path: Path) -> list[tuple[int, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise PrecursorError("cannot read %s: %s" % (path, exc)) from exc
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise PrecursorError("%s: invalid JSON: %s" % (path, exc)) from exc
        if not isinstance(payload, list):
            raise PrecursorError("%s: JSON input must be an array of precursor objects" % path)
        return [(index + 1, item) for index, item in enumerate(payload)]
    records: list[tuple[int, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append((line_number, json.loads(line)))
        except ValueError as exc:
            raise PrecursorError("%s: line %d: invalid JSON: %s" % (path, line_number, exc)) from exc
    return records


def _build(record: Any, line_number: int) -> Precursor:
    prefix = "line %d: " % line_number
    if not isinstance(record, dict):
        raise PrecursorError(prefix + "precursor must be an object")
    name = record.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PrecursorError(prefix + "name must be a non-empty string")
    if "sequence" not in record:
        raise PrecursorError(prefix + "sequence is required")
    try:
        sequence = validate_precursor_sequence(record["sequence"])
    except PrecursorError as exc:
        raise PrecursorError(prefix + str(exc)) from exc
    source = record.get("source", "user-supplied")
    if not isinstance(source, str) or not source.strip():
        raise PrecursorError(prefix + "source must be a non-empty string")
    uniprot = record.get("uniprot", "")
    if not isinstance(uniprot, str):
        raise PrecursorError(prefix + "uniprot must be a string")
    notes = record.get("notes", "")
    if not isinstance(notes, str):
        raise PrecursorError(prefix + "notes must be a string")
    return Precursor(
        precursor_id=precursor_id_for(sequence),
        name=name.strip(),
        sequence=sequence,
        length=len(sequence),
        source=source.strip(),
        uniprot=uniprot.strip(),
        notes=notes,
        created_at=clock.iso(),
    )


def import_precursors(store: Store, path: Path, dry_run: bool = False) -> dict[str, Any]:
    records = _load_records(Path(path))
    precursors: list[Precursor] = []
    seen: set[str] = set()
    for line_number, record in records:
        precursor = _build(record, line_number)
        if precursor.precursor_id in seen:
            raise PrecursorError("line %d: duplicate precursor id %s" % (line_number, precursor.precursor_id))
        exists = store.conn.execute(
            "SELECT 1 FROM precursors WHERE precursor_id=?", (precursor.precursor_id,)
        ).fetchone()
        if exists:
            raise PrecursorError(
                "line %d: precursor %s already exists; ledger rows are append-only"
                % (line_number, precursor.precursor_id)
            )
        seen.add(precursor.precursor_id)
        precursors.append(precursor)

    if not dry_run:
        try:
            for precursor in precursors:
                store.add_precursor(precursor, commit=False)
            store.conn.commit()
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
            store.conn.rollback()
            raise PrecursorError("precursor import rolled back: %s" % exc) from exc
    return {
        "imported": len(precursors),
        "dry_run": dry_run,
        "precursors": [precursor.to_dict() for precursor in precursors],
    }


def precursor_rows(store: Store) -> list[dict[str, Any]]:
    return [dict(row) for row in store.conn.execute("SELECT * FROM precursors ORDER BY precursor_id")]


def get_precursor(store: Store, precursor_id: str) -> dict[str, Any] | None:
    row = store.conn.execute("SELECT * FROM precursors WHERE precursor_id=?", (precursor_id,)).fetchone()
    return dict(row) if row is not None else None


def format_precursors(precursors: list[dict[str, Any]]) -> str:
    if not precursors:
        return "no precursors in this database"
    lines = ["%-16s %-24s %6s %-16s %s" % ("precursor", "name", "len", "uniprot", "source")]
    for precursor in precursors:
        lines.append(
            "%-16s %-24s %6d %-16s %s"
            % (
                precursor["precursor_id"],
                precursor["name"][:24],
                precursor["length"],
                precursor.get("uniprot") or "-",
                precursor.get("source", ""),
            )
        )
    return "\n".join(lines)


def format_precursor(precursor: dict[str, Any]) -> str:
    return "\n".join(
        [
            "%s  %s" % (precursor["precursor_id"], precursor["name"]),
            "sequence:  %s" % precursor["sequence"],
            "length:    %d" % precursor["length"],
            "source:    %s" % precursor.get("source", ""),
            "uniprot:   %s" % (precursor.get("uniprot") or "-"),
            "notes:     %s" % (precursor.get("notes") or "-"),
            "note: user-supplied sequence; cleavage is an explicit generation operation",
        ]
    )
