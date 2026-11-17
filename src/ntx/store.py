"""SQLite persistence with a provenance invariant.

``Store.provenance_errors()`` returns every claim whose exact span is not a
substring of its document text. Tests assert this list is empty, and the
report command refuses to render a database that fails the check.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import (
    Appraisal,
    Candidate,
    CandidateLineage,
    Claim,
    Document,
    Experiment,
    Mention,
    Outcome,
    Precursor,
    Prediction,
)
from .quant import EFFECT_TYPES, RATIO_TYPES, effect_columns, validate_quant

SCHEMA_VERSION = 8


def _claim_effect(claim: Claim) -> dict[str, Any]:
    """Prefer explicit claim columns, falling back to the primary quant effect."""
    if not claim.effect_type and claim.effect_size is None:
        return effect_columns(claim.quant)
    if not claim.effect_type or claim.effect_size is None:
        raise ValueError("effect_type and effect_size must be set together")
    if claim.effect_type not in EFFECT_TYPES:
        raise ValueError("unknown effect_type: %s" % claim.effect_type)
    value = float(claim.effect_size)
    if not math.isfinite(value):
        raise ValueError("effect_size must be finite")
    if claim.effect_type in RATIO_TYPES and value <= 0:
        raise ValueError("ratio effect_size must be positive")
    if claim.effect_type == "correlation" and not -1.0 <= value <= 1.0:
        raise ValueError("correlation effect_size must be in [-1, 1]")
    low, high = claim.effect_ci_low, claim.effect_ci_high
    if (low is None) != (high is None):
        raise ValueError("effect CI requires both bounds")
    if low is not None and high is not None:
        if not math.isfinite(float(low)) or not math.isfinite(float(high)):
            raise ValueError("effect CI bounds must be finite")
        if float(low) > float(high):
            raise ValueError("effect CI is inverted")
    return {
        "effect_type": claim.effect_type,
        "effect_size": value,
        "effect_ci_low": float(low) if low is not None else None,
        "effect_ci_high": float(high) if high is not None else None,
    }


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    source TEXT,
    pmid TEXT,
    pmcid TEXT,
    doi TEXT,
    title TEXT,
    abstract TEXT,
    journal TEXT,
    year INTEGER,
    pubtypes TEXT,
    retraction_status TEXT,
    retraction_note TEXT,
    license TEXT,
    raw_path TEXT,
    content_hash TEXT,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,
    class TEXT,
    pref_name TEXT,
    uniprot TEXT,
    chebi TEXT,
    pubchem_cid TEXT,
    mesh TEXT,
    synonyms TEXT
);
CREATE TABLE IF NOT EXISTS mentions (
    mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    surface TEXT,
    start INTEGER,
    end INTEGER,
    section TEXT,
    method TEXT,
    confidence REAL,
    accepted INTEGER,
    reason TEXT,
    UNIQUE(doc_id, entity_id, start, end)
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    direction TEXT,
    outcome TEXT,
    species TEXT,
    design TEXT,
    sample_size INTEGER,
    p_value REAL,
    effect_type TEXT DEFAULT '',
    effect_size REAL,
    effect_ci_low REAL,
    effect_ci_high REAL,
    quant TEXT DEFAULT '{}',
    hedged INTEGER,
    negated INTEGER,
    sentence TEXT,
    start INTEGER,
    end INTEGER,
    extractor TEXT,
    confidence REAL,
    role TEXT DEFAULT 'intervention',
    evidence_start INTEGER DEFAULT 0,
    evidence_end INTEGER DEFAULT 0,
    section TEXT DEFAULT 'abstract',
    resolver TEXT DEFAULT 'direct',
    reasons TEXT DEFAULT '[]',
    UNIQUE(doc_id, entity_id, start, end)
);
CREATE TABLE IF NOT EXISTS document_texts (
    doc_id TEXT PRIMARY KEY,
    pmcid TEXT NOT NULL,
    source_url TEXT NOT NULL,
    text TEXT NOT NULL,
    sections TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    license TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fulltext_claims (
    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    direction TEXT,
    outcome TEXT,
    species TEXT,
    design TEXT,
    sample_size INTEGER,
    p_value REAL,
    effect_type TEXT DEFAULT '',
    effect_size REAL,
    effect_ci_low REAL,
    effect_ci_high REAL,
    quant TEXT DEFAULT '{}',
    hedged INTEGER,
    negated INTEGER,
    sentence TEXT,
    start INTEGER,
    end INTEGER,
    extractor TEXT,
    confidence REAL,
    role TEXT DEFAULT 'intervention',
    evidence_start INTEGER DEFAULT 0,
    evidence_end INTEGER DEFAULT 0,
    section TEXT DEFAULT 'other',
    resolver TEXT DEFAULT 'direct',
    reasons TEXT DEFAULT '[]',
    text_hash TEXT NOT NULL,
    UNIQUE(doc_id, entity_id, start, end)
);
CREATE TABLE IF NOT EXISTS appraisals (
    appraisal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    start INTEGER,
    end INTEGER,
    design TEXT,
    retraction_status TEXT,
    bias_flags TEXT,
    funding_flag INTEGER,
    coi_flag INTEGER,
    rubric_points REAL,
    band TEXT,
    reasons TEXT,
    UNIQUE(doc_id, entity_id, start, end)
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    start INTEGER,
    end INTEGER,
    reviewer TEXT,
    decision TEXT,
    note TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started TEXT,
    finished TEXT,
    config_hash TEXT,
    code_version TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS manifests (
    run_id TEXT PRIMARY KEY,
    created TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sequence TEXT NOT NULL,
    length INTEGER NOT NULL,
    target_entity_id TEXT DEFAULT '',
    hypothesis_id TEXT DEFAULT '',
    modifications TEXT DEFAULT '[]',
    properties TEXT DEFAULT '{}',
    provenance TEXT DEFAULT 'user-supplied',
    notes TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS precursors (
    precursor_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sequence TEXT NOT NULL,
    length INTEGER NOT NULL,
    source TEXT DEFAULT 'user-supplied',
    uniprot TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS candidate_lineage (
    lineage_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    parent_id TEXT DEFAULT '',
    operation TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT DEFAULT '',
    start_pos INTEGER,
    end_pos INTEGER,
    parameters TEXT DEFAULT '{}',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL,
    candidate_id TEXT DEFAULT '',
    hypothesis_kind TEXT,
    entity_id TEXT,
    outcome TEXT,
    hypothesis_snapshot TEXT DEFAULT '{}',
    design TEXT,
    assay TEXT,
    primary_outcome TEXT,
    n_planned INTEGER DEFAULT 0,
    preregistration TEXT DEFAULT '',
    power_json TEXT DEFAULT '{}',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL UNIQUE,
    direction TEXT NOT NULL,
    probability REAL NOT NULL,
    rationale TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    observed_direction TEXT NOT NULL,
    effect_size REAL,
    ci_low REAL,
    ci_high REAL,
    n INTEGER,
    qc TEXT DEFAULT 'pass',
    note TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON claims(doc_id);
CREATE INDEX IF NOT EXISTS idx_claims_entity ON claims(entity_id);
CREATE INDEX IF NOT EXISTS idx_claims_role ON claims(role);
CREATE INDEX IF NOT EXISTS idx_mentions_doc ON mentions(doc_id);
CREATE INDEX IF NOT EXISTS idx_manifests_created ON manifests(created);
CREATE INDEX IF NOT EXISTS idx_experiments_hypothesis ON experiments(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_experiments_candidate ON experiments(candidate_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_experiment ON outcomes(experiment_id);
CREATE INDEX IF NOT EXISTS idx_candidate_lineage_candidate ON candidate_lineage(candidate_id);
CREATE INDEX IF NOT EXISTS idx_candidate_lineage_parent ON candidate_lineage(parent_id);
CREATE TRIGGER IF NOT EXISTS candidates_no_update BEFORE UPDATE ON candidates
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS candidates_no_delete BEFORE DELETE ON candidates
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiments_no_update BEFORE UPDATE ON experiments
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiments_no_delete BEFORE DELETE ON experiments
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS precursors_no_update BEFORE UPDATE ON precursors
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS precursors_no_delete BEFORE DELETE ON precursors
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS candidate_lineage_no_update BEFORE UPDATE ON candidate_lineage
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS candidate_lineage_no_delete BEFORE DELETE ON candidate_lineage
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
"""


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    """Baseline schema: tables created by SCHEMA above."""


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Add the run-manifest table used for reproducibility records."""
    conn.execute("CREATE TABLE IF NOT EXISTS manifests (run_id TEXT PRIMARY KEY, created TEXT, payload TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_manifests_created ON manifests(created)")


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    """Add role, evidence span, section, resolver and reason columns."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(claims)")}
    additions = {
        "role": "TEXT DEFAULT 'intervention'",
        "evidence_start": "INTEGER DEFAULT 0",
        "evidence_end": "INTEGER DEFAULT 0",
        "section": "TEXT DEFAULT 'abstract'",
        "resolver": "TEXT DEFAULT 'direct'",
        "reasons": "TEXT DEFAULT '[]'",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute("ALTER TABLE claims ADD COLUMN %s %s" % (name, ddl))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_role ON claims(role)")


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    """Add the append-only candidate, experiment, prediction and outcome ledger."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sequence TEXT NOT NULL,
            length INTEGER NOT NULL,
            target_entity_id TEXT DEFAULT '',
            hypothesis_id TEXT DEFAULT '',
            modifications TEXT DEFAULT '[]',
            properties TEXT DEFAULT '{}',
            provenance TEXT DEFAULT 'user-supplied',
            notes TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS experiments (
            experiment_id TEXT PRIMARY KEY,
            hypothesis_id TEXT NOT NULL,
            candidate_id TEXT DEFAULT '',
            hypothesis_kind TEXT,
            entity_id TEXT,
            outcome TEXT,
            hypothesis_snapshot TEXT DEFAULT '{}',
            design TEXT,
            assay TEXT,
            primary_outcome TEXT,
            n_planned INTEGER DEFAULT 0,
            preregistration TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL UNIQUE,
            direction TEXT NOT NULL,
            probability REAL NOT NULL,
            rationale TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS outcomes (
            outcome_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            observed_direction TEXT NOT NULL,
            effect_size REAL,
            ci_low REAL,
            ci_high REAL,
            n INTEGER,
            qc TEXT DEFAULT 'pass',
            note TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_experiments_hypothesis ON experiments(hypothesis_id);
        CREATE INDEX IF NOT EXISTS idx_experiments_candidate ON experiments(candidate_id);
        CREATE INDEX IF NOT EXISTS idx_outcomes_experiment ON outcomes(experiment_id);
        CREATE TRIGGER IF NOT EXISTS candidates_no_update BEFORE UPDATE ON candidates
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS candidates_no_delete BEFORE DELETE ON candidates
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS experiments_no_update BEFORE UPDATE ON experiments
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS experiments_no_delete BEFORE DELETE ON experiments
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        """
    )


def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    """Add precursor records and structured generation lineage."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS precursors (
            precursor_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sequence TEXT NOT NULL,
            length INTEGER NOT NULL,
            source TEXT DEFAULT 'user-supplied',
            uniprot TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS candidate_lineage (
            lineage_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            parent_id TEXT DEFAULT '',
            operation TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            start_pos INTEGER,
            end_pos INTEGER,
            parameters TEXT DEFAULT '{}',
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_lineage_candidate ON candidate_lineage(candidate_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_lineage_parent ON candidate_lineage(parent_id);
        CREATE TRIGGER IF NOT EXISTS precursors_no_update BEFORE UPDATE ON precursors
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS precursors_no_delete BEFORE DELETE ON precursors
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS candidate_lineage_no_update BEFORE UPDATE ON candidate_lineage
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS candidate_lineage_no_delete BEFORE DELETE ON candidate_lineage
        BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        """
    )


def _migrate_to_v6(conn: sqlite3.Connection) -> None:
    """Add registration power worksheets and external outcome identities."""
    for table, name, ddl in (
        ("experiments", "power_json", "TEXT DEFAULT '{}'"),
        ("outcomes", "source_id", "TEXT DEFAULT ''"),
    ):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        if name not in columns:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, ddl))
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_outcomes_source "
        "ON outcomes(experiment_id, source_id) WHERE source_id <> ''"
    )


def _migrate_to_v7(conn: sqlite3.Connection) -> None:
    """Add local full-text snapshots and their derived claim layer."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS document_texts (
            doc_id TEXT PRIMARY KEY,
            pmcid TEXT NOT NULL,
            source_url TEXT NOT NULL,
            text TEXT NOT NULL,
            sections TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            license TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS fulltext_claims (
            claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            direction TEXT,
            outcome TEXT,
            species TEXT,
            design TEXT,
            sample_size INTEGER,
            p_value REAL,
            hedged INTEGER,
            negated INTEGER,
            sentence TEXT,
            start INTEGER,
            end INTEGER,
            extractor TEXT,
            confidence REAL,
            role TEXT DEFAULT 'intervention',
            evidence_start INTEGER DEFAULT 0,
            evidence_end INTEGER DEFAULT 0,
            section TEXT DEFAULT 'other',
            resolver TEXT DEFAULT 'direct',
            reasons TEXT DEFAULT '[]',
            text_hash TEXT NOT NULL,
            UNIQUE(doc_id, entity_id, start, end)
        );
        """
    )


def _migrate_to_v8(conn: sqlite3.Connection) -> None:
    """Add conservative quantitative effect and context fields."""
    additions = {
        "effect_type": "TEXT DEFAULT ''",
        "effect_size": "REAL",
        "effect_ci_low": "REAL",
        "effect_ci_high": "REAL",
        "quant": "TEXT DEFAULT '{}'",
    }
    for table in ("claims", "fulltext_claims"):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, ddl))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_effect_type ON claims(effect_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fulltext_claims_effect_type ON fulltext_claims(effect_type)")


MIGRATIONS = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
    5: _migrate_to_v5,
    6: _migrate_to_v6,
    7: _migrate_to_v7,
    8: _migrate_to_v8,
}


def _quant_errors(sentence: str, start: int, raw: Any) -> list[str]:
    try:
        quant = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return ["quant is not valid JSON"]
    return validate_quant(sentence, start, quant)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    # ---------------------------------------------------------- schema version
    def _migrate(self) -> None:
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        while version < SCHEMA_VERSION:
            migration = MIGRATIONS.get(version + 1)
            if migration is not None:
                migration(self.conn)
            version += 1
            self.conn.execute("PRAGMA user_version=%d" % version)

    @property
    def schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- inserts
    def add_entity(self, entity: dict[str, Any], commit: bool = True) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO entities (entity_id, class, pref_name, uniprot, chebi,"
            " pubchem_cid, mesh, synonyms) VALUES (?,?,?,?,?,?,?,?)",
            (
                entity.get("id"),
                entity.get("class"),
                entity.get("name"),
                entity.get("uniprot", ""),
                entity.get("chebi", ""),
                entity.get("pubchem_cid", ""),
                entity.get("mesh", ""),
                json.dumps(entity.get("synonyms", [])),
            ),
        )
        if commit:
            self.conn.commit()

    def add_document(self, doc: Document, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc.doc_id,
                doc.source,
                doc.pmid,
                doc.pmcid,
                doc.doi,
                doc.title,
                doc.abstract,
                doc.journal,
                doc.year,
                json.dumps(doc.pubtypes),
                doc.retraction_status,
                doc.retraction_note,
                doc.license,
                doc.raw_path,
                doc.content_hash,
                doc.fetched_at,
            ),
        )
        self.conn.commit()

    def add_mentions(self, mentions: Iterable[Mention], commit: bool = True) -> int:
        rows = [
            (
                m.doc_id,
                m.entity_id,
                m.surface,
                m.start,
                m.end,
                m.section,
                m.method,
                m.confidence,
                1 if m.accepted else 0,
                m.reason,
            )
            for m in mentions
        ]
        self.conn.executemany(
            "INSERT OR IGNORE INTO mentions (doc_id, entity_id, surface, start, end, section,"
            " method, confidence, accepted, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        if commit:
            self.conn.commit()
        return len(rows)

    def add_claims(self, claims: Iterable[Claim], commit: bool = True) -> int:
        rows = []
        for c in claims:
            effect = _claim_effect(c)
            rows.append(
                (
                    c.doc_id,
                    c.entity_id,
                    c.direction,
                    c.outcome,
                    c.species,
                    c.design,
                    c.sample_size,
                    c.p_value,
                    effect["effect_type"],
                    effect["effect_size"],
                    effect["effect_ci_low"],
                    effect["effect_ci_high"],
                    json.dumps(c.quant, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    if c.quant
                    else "{}",
                    1 if c.hedged else 0,
                    1 if c.negated else 0,
                    c.sentence,
                    c.start,
                    c.end,
                    c.extractor,
                    c.confidence,
                    c.role,
                    c.evidence_start,
                    c.evidence_end,
                    c.section,
                    c.resolver,
                    json.dumps(c.reasons),
                )
            )
        self.conn.executemany(
            "INSERT OR IGNORE INTO claims (doc_id, entity_id, direction, outcome, species, design,"
            " sample_size, p_value, effect_type, effect_size, effect_ci_low, effect_ci_high, quant,"
            " hedged, negated, sentence, start, end, extractor, confidence, role, evidence_start,"
            " evidence_end, section, resolver, reasons)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        if commit:
            self.conn.commit()
        return len(rows)

    def add_document_text(
        self,
        *,
        doc_id: str,
        pmcid: str,
        source_url: str,
        text: str,
        sections: list[dict[str, Any]] | str,
        content_hash: str,
        fetched_at: str,
        license: str = "",
        commit: bool = True,
    ) -> None:
        """Store one immutable full-text snapshot."""
        if not content_hash:
            raise ValueError("content_hash is required")
        payload = sections if isinstance(sections, str) else json.dumps(sections, separators=(",", ":"))
        self.conn.execute(
            "INSERT INTO document_texts (doc_id, pmcid, source_url, text, sections, content_hash,"
            " fetched_at, license) VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, pmcid, source_url, text, payload, content_hash, fetched_at, license),
        )
        if commit:
            self.conn.commit()

    def add_fulltext_claims(self, claims: Iterable[Claim], *, text_hash: str, commit: bool = True) -> int:
        """Insert full-text claims, ignoring spans that already exist."""
        rows = []
        for c in claims:
            effect = _claim_effect(c)
            rows.append(
                (
                    c.doc_id,
                    c.entity_id,
                    c.direction,
                    c.outcome,
                    c.species,
                    c.design,
                    c.sample_size,
                    c.p_value,
                    effect["effect_type"],
                    effect["effect_size"],
                    effect["effect_ci_low"],
                    effect["effect_ci_high"],
                    json.dumps(c.quant, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    if c.quant
                    else "{}",
                    1 if c.hedged else 0,
                    1 if c.negated else 0,
                    c.sentence,
                    c.start,
                    c.end,
                    c.extractor,
                    c.confidence,
                    c.role,
                    c.evidence_start,
                    c.evidence_end,
                    c.section,
                    c.resolver,
                    json.dumps(c.reasons),
                    text_hash,
                )
            )
        before = int(self.conn.execute("SELECT COUNT(*) FROM fulltext_claims").fetchone()[0])
        self.conn.executemany(
            "INSERT OR IGNORE INTO fulltext_claims (doc_id, entity_id, direction, outcome, species,"
            " design, sample_size, p_value, effect_type, effect_size, effect_ci_low, effect_ci_high,"
            " quant, hedged, negated, sentence, start, end, extractor, confidence, role,"
            " evidence_start, evidence_end, section, resolver, reasons, text_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        after = int(self.conn.execute("SELECT COUNT(*) FROM fulltext_claims").fetchone()[0])
        if commit:
            self.conn.commit()
        return after - before

    def add_appraisals(self, appraisals: Iterable[Appraisal], commit: bool = True) -> int:
        rows = [
            (
                a.doc_id,
                a.entity_id,
                a.start,
                a.end,
                a.design,
                a.retraction_status,
                json.dumps(a.bias_flags),
                1 if a.funding_flag else 0,
                1 if a.coi_flag else 0,
                a.rubric_points,
                a.band,
                json.dumps(a.reasons),
            )
            for a in appraisals
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO appraisals (doc_id, entity_id, start, end, design,"
            " retraction_status, bias_flags, funding_flag, coi_flag, rubric_points, band, reasons)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        if commit:
            self.conn.commit()
        return len(rows)

    # ---------------------------------------------------------------- ledger
    def add_candidate(self, candidate: Candidate, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO candidates (candidate_id, name, sequence, length, target_entity_id,"
            " hypothesis_id, modifications, properties, provenance, notes, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate.candidate_id,
                candidate.name,
                candidate.sequence,
                candidate.length,
                candidate.target_entity_id,
                candidate.hypothesis_id,
                json.dumps(candidate.modifications),
                json.dumps(candidate.properties, sort_keys=True),
                candidate.provenance,
                candidate.notes,
                candidate.created_at,
            ),
        )
        if commit:
            self.conn.commit()

    def add_precursor(self, precursor: Precursor, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO precursors (precursor_id, name, sequence, length, source, uniprot,"
            " notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                precursor.precursor_id,
                precursor.name,
                precursor.sequence,
                precursor.length,
                precursor.source,
                precursor.uniprot,
                precursor.notes,
                precursor.created_at,
            ),
        )
        if commit:
            self.conn.commit()

    def add_lineage(self, lineage: CandidateLineage, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO candidate_lineage (lineage_id, candidate_id, parent_id, operation,"
            " source_kind, source_id, start_pos, end_pos, parameters, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                lineage.lineage_id,
                lineage.candidate_id,
                lineage.parent_id,
                lineage.operation,
                lineage.source_kind,
                lineage.source_id,
                lineage.start,
                lineage.end,
                json.dumps(lineage.parameters, sort_keys=True),
                lineage.created_at,
            ),
        )
        if commit:
            self.conn.commit()

    def add_experiment(self, experiment: Experiment, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO experiments (experiment_id, hypothesis_id, candidate_id, hypothesis_kind,"
            " entity_id, outcome, hypothesis_snapshot, design, assay, primary_outcome, n_planned,"
            " preregistration, power_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                experiment.experiment_id,
                experiment.hypothesis_id,
                experiment.candidate_id,
                experiment.hypothesis_kind,
                experiment.entity_id,
                experiment.outcome,
                json.dumps(experiment.hypothesis_snapshot, sort_keys=True),
                experiment.design,
                experiment.assay,
                experiment.primary_outcome,
                experiment.n_planned,
                experiment.preregistration,
                json.dumps(experiment.power, sort_keys=True),
                experiment.created_at,
            ),
        )
        if commit:
            self.conn.commit()

    def add_prediction(self, prediction: Prediction, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO predictions (prediction_id, experiment_id, direction, probability,"
            " rationale, created_at) VALUES (?,?,?,?,?,?)",
            (
                prediction.prediction_id,
                prediction.experiment_id,
                prediction.direction,
                prediction.probability,
                prediction.rationale,
                prediction.created_at,
            ),
        )
        if commit:
            self.conn.commit()

    def add_outcome(self, outcome: Outcome, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO outcomes (outcome_id, experiment_id, observed_direction, effect_size,"
            " ci_low, ci_high, n, qc, note, source_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                outcome.outcome_id,
                outcome.experiment_id,
                outcome.observed_direction,
                outcome.effect_size,
                outcome.ci_low,
                outcome.ci_high,
                outcome.n,
                outcome.qc,
                outcome.note,
                outcome.source_id,
                outcome.created_at,
            ),
        )
        if commit:
            self.conn.commit()

    # -------------------------------------------------------------- manifests
    def add_manifest(self, manifest: Any) -> None:
        """Store a run manifest (duck-typed: needs run_id/created/to_json)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO manifests (run_id, created, payload) VALUES (?,?,?)",
            (manifest.run_id, manifest.created, manifest.to_json()),
        )
        self.conn.commit()

    def manifests(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM manifests ORDER BY created"))

    # ----------------------------------------------------------------- reads
    def get_document(self, doc_id: str) -> Document | None:
        row = self.conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if row is None:
            return None
        return Document(
            doc_id=row["doc_id"],
            source=row["source"],
            pmid=row["pmid"],
            pmcid=row["pmcid"],
            doi=row["doi"],
            title=row["title"],
            abstract=row["abstract"],
            journal=row["journal"],
            year=row["year"],
            pubtypes=json.loads(row["pubtypes"] or "[]"),
            retraction_status=row["retraction_status"],
            retraction_note=row["retraction_note"],
            license=row["license"],
            raw_path=row["raw_path"],
            content_hash=row["content_hash"],
            fetched_at=row["fetched_at"],
        )

    def documents(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM documents"))

    def claims(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM claims"))

    def document_texts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM document_texts ORDER BY doc_id")
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["sections"] = json.loads(item["sections"])
            out.append(item)
        return out

    def get_document_text(self, doc_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM document_texts WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["sections"] = json.loads(item["sections"])
        return item

    def fulltext_claims(self, doc_id: str | None = None) -> list[sqlite3.Row]:
        if doc_id is None:
            return list(self.conn.execute("SELECT * FROM fulltext_claims ORDER BY doc_id, claim_id"))
        return list(
            self.conn.execute("SELECT * FROM fulltext_claims WHERE doc_id = ? ORDER BY claim_id", (doc_id,))
        )

    def appraisals(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM appraisals"))

    def counts(self) -> dict[str, int]:
        out = {}
        for table in ("documents", "entities", "mentions", "claims", "appraisals"):
            out[table] = int(self.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
        out["fulltext_documents"] = int(
            self.conn.execute("SELECT COUNT(*) FROM document_texts").fetchone()[0]
        )
        out["fulltext_claims"] = int(self.conn.execute("SELECT COUNT(*) FROM fulltext_claims").fetchone()[0])
        return out

    def provenance_errors(self) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        rows = self.conn.execute(
            "SELECT c.claim_id, c.doc_id, c.start, c.end, c.sentence, c.evidence_start,"
            " c.evidence_end, c.quant, d.title, d.abstract"
            " FROM claims c JOIN documents d ON d.doc_id = c.doc_id"
        )
        for row in rows:
            text = (row["title"] + "\n\n" + row["abstract"]).strip()
            start, end = row["start"], row["end"]
            if start is None or end is None or start < 0 or end > len(text) or start >= end:
                errors.append({"claim_id": row["claim_id"], "doc_id": row["doc_id"], "reason": "bad span"})
                continue
            if text[start:end] != row["sentence"]:
                errors.append(
                    {"claim_id": row["claim_id"], "doc_id": row["doc_id"], "reason": "span mismatch"}
                )
                continue
            evidence_start, evidence_end = row["evidence_start"], row["evidence_end"]
            if evidence_start and evidence_end:
                if evidence_start < start or evidence_end > end or evidence_start >= evidence_end:
                    errors.append(
                        {
                            "claim_id": row["claim_id"],
                            "doc_id": row["doc_id"],
                            "reason": "evidence span outside sentence",
                        }
                    )
                elif text[evidence_start:evidence_end] not in row["sentence"]:
                    errors.append(
                        {
                            "claim_id": row["claim_id"],
                            "doc_id": row["doc_id"],
                            "reason": "evidence span mismatch",
                        }
                    )
            for message in _quant_errors(row["sentence"], start, row["quant"]):
                errors.append(
                    {
                        "claim_id": row["claim_id"],
                        "doc_id": row["doc_id"],
                        "reason": "quant: " + message,
                    }
                )
        return errors

    def fulltext_provenance_errors(self) -> list[dict[str, Any]]:
        """Check every full-text claim against its exact stored snapshot."""
        errors: list[dict[str, Any]] = []
        rows = self.conn.execute(
            "SELECT c.claim_id, c.doc_id, c.start, c.end, c.sentence, c.evidence_start,"
            " c.evidence_end, c.quant, c.text_hash, t.text, t.content_hash"
            " FROM fulltext_claims c JOIN document_texts t ON t.doc_id = c.doc_id"
        )
        for row in rows:
            item = {
                "claim_id": row["claim_id"],
                "doc_id": row["doc_id"],
                "source": "fulltext",
            }
            if row["text_hash"] != row["content_hash"]:
                errors.append({**item, "reason": "text hash mismatch"})
                continue
            text = row["text"]
            start, end = row["start"], row["end"]
            if start is None or end is None or start < 0 or end > len(text) or start >= end:
                errors.append({**item, "reason": "bad span"})
                continue
            if text[start:end] != row["sentence"]:
                errors.append({**item, "reason": "span mismatch"})
                continue
            evidence_start, evidence_end = row["evidence_start"], row["evidence_end"]
            if evidence_start and evidence_end:
                if evidence_start < start or evidence_end > end or evidence_start >= evidence_end:
                    errors.append({**item, "reason": "evidence span outside sentence"})
                elif text[evidence_start:evidence_end] not in row["sentence"]:
                    errors.append({**item, "reason": "evidence span mismatch"})
            for message in _quant_errors(row["sentence"], start, row["quant"]):
                errors.append({**item, "reason": "quant: " + message})
        return errors
