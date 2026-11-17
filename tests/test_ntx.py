"""Tests for the ntx prototype (stdlib unittest; no network)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("NTX_ROOT", str(ROOT))

from ntx import ablate as ablate_mod  # noqa: E402
from ntx import (  # noqa: E402
    annotation,
    build_paths,
    calibration_html,  # noqa: E402
    clock,
    config,
    discovery,  # noqa: E402
    manifest,
    stats,
)
from ntx import audit as audit_mod  # noqa: E402
from ntx import calibration as calibration_mod  # noqa: E402
from ntx import candidates as candidates_mod  # noqa: E402
from ntx import cli as cli_mod
from ntx import corpus as corpus_mod  # noqa: E402
from ntx import dashboard as dashboard_mod  # noqa: E402
from ntx import dbadmin as dbadmin_mod  # noqa: E402
from ntx import eval as eval_mod  # noqa: E402
from ntx import fulltext as fulltext_mod  # noqa: E402
from ntx import generate as generate_mod  # noqa: E402
from ntx import http as http_mod  # noqa: E402
from ntx import ingest as ingest_mod  # noqa: E402
from ntx import ledger as ledger_mod  # noqa: E402
from ntx import log as log_mod  # noqa: E402
from ntx import mechanism as mechanism_mod  # noqa: E402
from ntx import openapi as openapi_mod  # noqa: E402
from ntx import power as power_mod  # noqa: E402
from ntx import precursors as precursors_mod  # noqa: E402
from ntx import quality as quality_mod  # noqa: E402
from ntx import quant as quant_mod  # noqa: E402
from ntx import reconcile as reconcile_mod  # noqa: E402
from ntx import report as report_mod  # noqa: E402
from ntx import serve as serve_mod  # noqa: E402
from ntx import settings as settings_mod  # noqa: E402
from ntx import synthesis as synthesis_mod  # noqa: E402
from ntx import verify as verify_mod  # noqa: E402
from ntx.appraise import (  # noqa: E402
    consistency,
    design_from_pubtypes,
    merge_crossref_status,
    retraction_from_pubtypes,
    rubric,
)
from ntx.config import ontology  # noqa: E402
from ntx.extract import FeatureFlags, extract_claims, sections, split_clauses, split_sentences  # noqa: E402
from ntx.models import Candidate, Claim, Document, Precursor  # noqa: E402
from ntx.nlp import Lexicon, document_flags  # noqa: E402
from ntx.pipeline import process_documents  # noqa: E402
from ntx.store import SCHEMA_VERSION, Store  # noqa: E402

LEX = Lexicon(ontology())


class TestLexicon(unittest.TestCase):
    def test_generic_words_are_not_entities(self):
        mentions = LEX.detect("The neuropeptide and peptide levels were measured.")
        self.assertEqual(mentions, [])

    def test_longest_match_wins(self):
        mentions = LEX.detect("Neuropeptide Y (NPY) increased feeding.")
        ids = [m.entity_id for m in mentions]
        self.assertIn("NP:NPY", ids)
        self.assertNotIn("NP:NT", ids)

    def test_ambiguous_acronym_requires_expansion(self):
        without = LEX.detect("OT was scheduled twice weekly.")
        self.assertEqual([m.entity_id for m in without], [])
        with_expansion = LEX.detect("Oxytocin (OT) was administered intranasally.")
        self.assertIn("NP:OXT", [m.entity_id for m in with_expansion])

    def test_unambiguous_acronym_is_accepted(self):
        mentions = LEX.detect("BDNF levels correlated with memory.")
        self.assertIn("NTF:BDNF", [m.entity_id for m in mentions])

    def test_neurotropic_flag(self):
        flags = document_flags("Rabies is a neurotropic virus; neurotrophic factors differ.")
        self.assertTrue(any("neurotropic" in f and "neurotrophic" in f for f in flags))

    def test_spans_are_exact(self):
        text = "Intranasal oxytocin improved social cognition."
        for mention in LEX.detect(text):
            self.assertEqual(text[mention.start : mention.end], mention.surface)


class TestExtraction(unittest.TestCase):
    def _doc(self):
        text = (
            "Intranasal oxytocin improved social cognition in 120 participants "
            "(p = 0.01) in a randomized placebo-controlled trial."
        )
        mentions = LEX.detect(text)
        return text, mentions

    def test_direction_and_fields(self):
        text, mentions = self._doc()
        claims = extract_claims(text, mentions)
        self.assertTrue(claims)
        claim = claims[0]
        self.assertEqual(claim.direction, "increase")
        self.assertEqual(claim.outcome, "social_cognition")
        self.assertEqual(claim.species, "human")
        self.assertEqual(claim.design, "rct")
        self.assertEqual(claim.sample_size, 120)
        self.assertAlmostEqual(claim.p_value, 0.01)

    def test_negation(self):
        text = "Oxytocin did not improve social cognition in adults."
        claims = extract_claims(text, LEX.detect(text))
        self.assertTrue(claims)
        self.assertEqual(claims[0].direction, "none")
        self.assertTrue(claims[0].negated)

    def test_claims_span_exactly_the_sentence(self):
        text, mentions = self._doc()
        for claim in extract_claims(text, mentions):
            self.assertEqual(text[claim.start : claim.end], claim.sentence)

    def test_sentence_spans_are_contiguous(self):
        text = "First sentence here. Second sentence follows! Third sentence is long enough?"
        spans = split_sentences(text)
        self.assertEqual(len(spans), 3)
        for start, end in spans:
            self.assertTrue(text[start:end])


class TestQuantitativeDetails(unittest.TestCase):
    def test_smd_with_ci_and_context(self):
        text = (
            "Intranasal oxytocin reduced anxiety compared with placebo "
            "(Cohen's d = -0.42, 95% CI -0.71 to -0.13)."
        )
        quant = quant_mod.extract_quant(text, 0)
        self.assertEqual(quant_mod.validate_quant(text, 0, quant), [])
        effect = quant_mod.primary_effect(quant)
        self.assertIsNotNone(effect)
        assert effect is not None
        self.assertEqual(effect["type"], "smd")
        self.assertAlmostEqual(effect["value"], -0.42)
        self.assertAlmostEqual(effect["ci_low"], -0.71)
        self.assertAlmostEqual(effect["ci_high"], -0.13)
        self.assertEqual(text[effect["start"] : effect["end"]], effect["quote"])
        self.assertEqual(quant["contexts"]["comparator"]["text"], "placebo")
        self.assertEqual(quant["contexts"]["route"]["text"], "Intranasal")

    def test_ratio_and_bracket_ci(self):
        text = "The odds ratio was 1.45 (95% CI 1.10 to 1.91) versus placebo."
        quant = quant_mod.extract_quant(text, 0)
        effect = quant_mod.primary_effect(quant)
        assert effect is not None
        self.assertEqual(effect["type"], "odds_ratio")
        self.assertAlmostEqual(effect["value"], 1.45)
        self.assertAlmostEqual(effect["ci_low"], 1.10)
        self.assertAlmostEqual(effect["ci_high"], 1.91)

    def test_percent_change_sign(self):
        positive = quant_mod.extract_quant("BDNF increased by 12% at 4 weeks.", 0)
        negative = quant_mod.extract_quant("BDNF decreased by 12% at 4 weeks.", 0)
        self.assertAlmostEqual(quant_mod.primary_effect(positive)["value"], 12.0)
        self.assertAlmostEqual(quant_mod.primary_effect(negative)["value"], -12.0)

    def test_no_label_means_no_effect(self):
        text = "Oxytocin improved trust in 40 adults."
        quant = quant_mod.extract_quant(text, 0)
        self.assertEqual(quant_mod.primary_effect(quant), None)
        self.assertNotIn("effects", quant)

    def test_invalid_ratio_is_rejected(self):
        text = "The odds ratio was 0.0 (95% CI 0.0 to 0.0)."
        quant = quant_mod.extract_quant(text, 0)
        self.assertEqual(quant_mod.primary_effect(quant), None)

    def test_validate_detects_tampered_quote(self):
        text = "Cohen's d = -0.42 (95% CI -0.71 to -0.13)."
        quant = quant_mod.extract_quant(text, 0)
        broken = json.loads(json.dumps(quant))
        broken["effects"][0]["quote"] = "tampered"
        self.assertTrue(quant_mod.validate_quant(text, 0, broken))

    def test_extraction_populates_claim_and_flag_disables(self):
        text = "Oxytocin reduced anxiety (Cohen's d = -0.42, 95% CI -0.71 to -0.13)."
        mentions = LEX.detect(text)
        claim = extract_claims(text, mentions)[0]
        self.assertEqual(claim.effect_type, "smd")
        self.assertAlmostEqual(claim.effect_size, -0.42)
        self.assertIn("effects", claim.quant)
        without = extract_claims(text, mentions, flags=FeatureFlags(quantitative=False))[0]
        self.assertEqual(without.effect_type, "")
        self.assertIsNone(without.effect_size)
        self.assertEqual(without.quant, {})

    def test_other_effect_labels(self):
        cases = [
            ("Mean difference was -1.2 (95% CI -2.0 to -0.4).", "mean_difference"),
            ("Risk ratio was 0.75 (95% CI 0.60 to 0.94).", "risk_ratio"),
            ("Hazard ratio was 0.80 (95% CI 0.70 to 0.92).", "hazard_ratio"),
            ("Beta = 0.31 (95% CI 0.10 to 0.52).", "beta"),
            ("Pearson's r = -0.44 (95% CI -0.60 to -0.25).", "correlation"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected):
                effect = quant_mod.primary_effect(quant_mod.extract_quant(text, 0))
                assert effect is not None
                self.assertEqual(effect["type"], expected)

    def test_inverted_ci_is_ignored(self):
        text = "The odds ratio was 1.45 (95% CI 2.00 to 1.00)."
        effect = quant_mod.primary_effect(quant_mod.extract_quant(text, 0))
        assert effect is not None
        self.assertIsNone(effect["ci_low"])
        self.assertIsNone(effect["ci_high"])

    def test_format_effect_and_empty_columns(self):
        text = "Intranasal oxytocin reduced anxiety compared with placebo (Cohen's d = -0.42)."
        quant = quant_mod.extract_quant(text, 0)
        rendered = quant_mod.format_effect(quant)
        self.assertIn("smd -0.42", rendered)
        self.assertIn("comparator: placebo", rendered)
        self.assertEqual(quant_mod.format_effect(None), "")
        self.assertEqual(quant_mod.effect_columns(None)["effect_type"], "")

    def test_validation_rejects_bad_context_and_type(self):
        text = "Oxytocin reduced anxiety (Cohen's d = -0.42)."
        quant = quant_mod.extract_quant(text, 0)
        bad = json.loads(json.dumps(quant))
        bad["effects"][0]["type"] = "not_a_type"
        bad["contexts"] = {"comparator": {"text": "wrong", "start": 0, "end": 5}}
        problems = quant_mod.validate_quant(text, 0, bad)
        self.assertTrue(any("unknown type" in problem for problem in problems))
        self.assertTrue(any("text does not match" in problem for problem in problems))
        self.assertEqual(quant_mod.validate_quant(text, 0, {}), [])
        self.assertEqual(quant_mod.validate_quant(text, 0, None), ["quant is not an object"])
        bad_ci = json.loads(json.dumps(quant))
        bad_ci["effects"][0]["ci_low"] = 1.0
        bad_ci["effects"][0]["ci_high"] = -1.0
        bad_ci["effects"][0]["start"] = 999
        self.assertTrue(
            any("inverted CI" in problem for problem in quant_mod.validate_quant(text, 0, bad_ci))
        )
        self.assertTrue(
            any(
                "outside the claim sentence" in problem
                for problem in quant_mod.validate_quant(text, 0, bad_ci)
            )
        )

    def test_ci_belongs_to_nearest_effect(self):
        text = "OR = 1.2, RR = 1.5 (95% CI 1.1 to 1.9)."
        quant = quant_mod.extract_quant(text, 0)
        effects = quant["effects"]
        self.assertEqual([effect["type"] for effect in effects], ["odds_ratio", "risk_ratio"])
        self.assertIsNone(effects[0]["ci_low"])
        self.assertAlmostEqual(effects[1]["ci_low"], 1.1)
        self.assertAlmostEqual(effects[1]["ci_high"], 1.9)

    def test_ci_before_next_effect_is_not_shared(self):
        text = "OR = 1.2, RR = 1.5 (95% CI 1.1 to 1.9), p = 0.04."
        quant = quant_mod.extract_quant(text, 0)
        self.assertIsNone(quant["effects"][0]["ci_low"])
        self.assertEqual(quant["effects"][1]["ci_high"], 1.9)

    def test_multiple_effects_are_flagged(self):
        text = "Cohen's d = 0.5 and OR = 1.4 (95% CI 1.1 to 1.8)."
        quant = quant_mod.extract_quant(text, 0)
        self.assertEqual(quant["schema"], quant_mod.QUANT_SCHEMA)
        self.assertIn("multiple_effects", quant["warnings"])
        self.assertIn("multiple_effect_types", quant["warnings"])
        self.assertEqual(quant_mod.validate_quant(text, 0, quant), [])

    def test_quant_schema_and_warning_validation(self):
        text = "Cohen's d = 0.5."
        quant = quant_mod.extract_quant(text, 0)
        bad_schema = json.loads(json.dumps(quant))
        bad_schema["schema"] = "ntx-quant-v99"
        self.assertTrue(
            any("unknown schema" in problem for problem in quant_mod.validate_quant(text, 0, bad_schema))
        )
        bad_warnings = json.loads(json.dumps(quant))
        bad_warnings["warnings"] = ["not_a_warning"]
        self.assertTrue(
            any("unknown warning" in problem for problem in quant_mod.validate_quant(text, 0, bad_warnings))
        )
        old = json.loads(json.dumps(quant))
        old.pop("schema")
        self.assertEqual(quant_mod.validate_quant(text, 0, old), [])

    def test_effect_domain_is_validated(self):
        text = "The odds ratio was 1.45 (95% CI 1.10 to 1.91)."
        quant = quant_mod.extract_quant(text, 0)
        bad = json.loads(json.dumps(quant))
        bad["effects"][0]["value"] = 0
        self.assertTrue(
            any("non-positive ratio" in problem for problem in quant_mod.validate_quant(text, 0, bad))
        )
        corr = quant_mod.extract_quant("Pearson's r = -0.44.", 0)
        bad_corr = json.loads(json.dumps(corr))
        bad_corr["effects"][0]["value"] = 2.0
        self.assertTrue(
            any(
                "correlation outside" in problem
                for problem in quant_mod.validate_quant("Pearson's r = -0.44.", 0, bad_corr)
            )
        )

    def test_quant_validation_reports_malformed_payloads(self):
        text = "Cohen's d = 0.5 (95% CI 0.2 to 0.8)."
        valid = quant_mod.extract_quant(text, 0)

        def problems(mutate):
            payload = json.loads(json.dumps(valid))
            mutate(payload)
            return quant_mod.validate_quant(text, 0, payload)

        cases = [
            (lambda payload: payload.__setitem__("effects", "bad"), "effects is not a list"),
            (lambda payload: payload.__setitem__("contexts", []), "contexts is not an object"),
            (lambda payload: payload.__setitem__("warnings", [1]), "warnings is not a list"),
            (lambda payload: payload["effects"].append("bad"), "is not an object"),
            (lambda payload: payload["effects"][0].__setitem__("type", "bogus"), "unknown type"),
            (lambda payload: payload["effects"][0].__setitem__("value", float("inf")), "no finite value"),
            (lambda payload: payload["effects"][0].__setitem__("ci_low", float("nan")), "non-finite"),
            (
                lambda payload: payload["effects"][0].update({"ci_low": 2.0, "ci_high": 1.0}),
                "inverted CI",
            ),
            (lambda payload: payload["effects"][0].__setitem__("start", "0"), "no integer span"),
            (
                lambda payload: payload["effects"][0].update({"start": 999, "end": 1000}),
                "outside the claim sentence",
            ),
            (lambda payload: payload["effects"][0].__setitem__("quote", "wrong"), "quote does not match"),
            (
                lambda payload: payload.setdefault("contexts", {}).__setitem__("dose", "bad"),
                "context.dose is not an object",
            ),
            (
                lambda payload: payload.setdefault("contexts", {}).__setitem__(
                    "dose", {"start": 0, "end": 1, "text": "x"}
                ),
                "context.dose text does not match",
            ),
            (
                lambda payload: payload.setdefault("contexts", {}).__setitem__(
                    "bogus", {"start": 0, "end": 1}
                ),
                "not a known context field",
            ),
            (
                lambda payload: payload.setdefault("contexts", {}).__setitem__(
                    "dose", {"start": "0", "end": 1}
                ),
                "has no integer span",
            ),
            (
                lambda payload: payload.setdefault("contexts", {}).__setitem__(
                    "dose", {"start": 100, "end": 105}
                ),
                "span is outside",
            ),
        ]
        for mutate, expected in cases:
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in problem for problem in problems(mutate)))

    def test_effect_columns_tolerates_malformed_primary(self):
        empty = quant_mod.effect_columns({"effects": [{"type": "smd", "value": "not-a-number"}]})
        self.assertEqual(empty["effect_type"], "")
        self.assertIsNone(empty["effect_size"])

    def test_store_roundtrip_and_provenance(self):
        text = "Oxytocin reduced anxiety (Cohen's d = -0.42, 95% CI -0.71 to -0.13)."
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                doc = Document(doc_id="PMID:1", abstract=text)
                store.add_document(doc)
                claim = extract_claims(text, LEX.detect(text))[0]
                claim.doc_id = doc.doc_id
                store.add_claims([claim])
                self.assertEqual(store.provenance_errors(), [])
                row = store.conn.execute("SELECT effect_type, effect_size, quant FROM claims").fetchone()
                self.assertEqual(row["effect_type"], "smd")
                self.assertAlmostEqual(row["effect_size"], -0.42)
                self.assertEqual(json.loads(row["quant"])["effects"][0]["type"], "smd")
            finally:
                store.close()

    def test_store_persists_explicit_effect_and_rejects_invalid_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                store.add_document(Document(doc_id="PMID:1", abstract="Oxytocin improved memory."))
                store.add_claims(
                    [
                        Claim(
                            doc_id="PMID:1",
                            entity_id="NP:OXT",
                            direction="increase",
                            outcome="memory",
                            sentence="Oxytocin improved memory.",
                            start=0,
                            end=24,
                            effect_type="smd",
                            effect_size=0.4,
                            effect_ci_low=0.1,
                            effect_ci_high=0.7,
                        )
                    ]
                )
                row = store.conn.execute("SELECT effect_type, effect_size FROM claims").fetchone()
                self.assertEqual(row["effect_type"], "smd")
                self.assertAlmostEqual(row["effect_size"], 0.4)
                bad = [
                    {"effect_type": "smd"},
                    {"effect_type": "bogus", "effect_size": 0.1},
                    {"effect_type": "smd", "effect_size": float("nan")},
                    {"effect_type": "odds_ratio", "effect_size": 0.0},
                    {"effect_type": "correlation", "effect_size": 2.0},
                    {"effect_type": "smd", "effect_size": 0.1, "effect_ci_low": 0.0},
                    {
                        "effect_type": "smd",
                        "effect_size": 0.1,
                        "effect_ci_low": 1.0,
                        "effect_ci_high": 0.0,
                    },
                ]
                for fields in bad:
                    with self.subTest(fields=fields), self.assertRaises(ValueError):
                        store.add_claims(
                            [
                                Claim(
                                    doc_id="PMID:1",
                                    entity_id="NP:OXT",
                                    direction="increase",
                                    outcome="memory",
                                    sentence="Oxytocin improved memory.",
                                    start=0,
                                    end=24,
                                    **fields,
                                )
                            ]
                        )
            finally:
                store.close()

    def test_v7_to_v8_migration_preserves_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            store = Store(db)
            try:
                store.add_document(Document(doc_id="PMID:1", title="T", abstract="Oxytocin increased trust."))
                store.conn.execute(
                    "INSERT INTO claims (doc_id, entity_id, direction, outcome, sentence, start,"
                    " end, extractor) VALUES (?,?,?,?,?,?,?,?)",
                    ("PMID:1", "NP:OXT", "increase", "trust", "Oxytocin increased trust.", 0, 25, "old"),
                )
                store.conn.commit()
                store.conn.executescript(
                    "DROP INDEX IF EXISTS idx_claims_effect_type;"
                    "DROP INDEX IF EXISTS idx_fulltext_claims_effect_type;"
                    "ALTER TABLE claims DROP COLUMN effect_type;"
                    "ALTER TABLE claims DROP COLUMN effect_size;"
                    "ALTER TABLE claims DROP COLUMN effect_ci_low;"
                    "ALTER TABLE claims DROP COLUMN effect_ci_high;"
                    "ALTER TABLE claims DROP COLUMN quant;"
                    "ALTER TABLE fulltext_claims DROP COLUMN effect_type;"
                    "ALTER TABLE fulltext_claims DROP COLUMN effect_size;"
                    "ALTER TABLE fulltext_claims DROP COLUMN effect_ci_low;"
                    "ALTER TABLE fulltext_claims DROP COLUMN effect_ci_high;"
                    "ALTER TABLE fulltext_claims DROP COLUMN quant;"
                    "PRAGMA user_version=7;"
                )
                store.conn.commit()
            finally:
                store.close()
            upgraded = Store(db)
            try:
                self.assertEqual(upgraded.schema_version, 8)
                self.assertEqual(upgraded.counts()["claims"], 1)
                columns = {row[1] for row in upgraded.conn.execute("PRAGMA table_info(claims)")}
                self.assertIn("quant", columns)
                self.assertIn("effect_size", columns)
            finally:
                upgraded.close()


class TestAppraisal(unittest.TestCase):
    def test_pubtype_design(self):
        self.assertEqual(design_from_pubtypes(["Meta-Analysis", "Journal Article"]), "meta_analysis")
        self.assertEqual(design_from_pubtypes(["Randomized Controlled Trial"]), "rct")

    def test_retraction_detection(self):
        self.assertEqual(retraction_from_pubtypes(["Retracted Publication"]), "retracted")
        self.assertEqual(retraction_from_pubtypes(["Retraction of Publication"]), "retraction_notice")

    def test_crossref_retraction_wins(self):
        payload = {"message": {"update-to": [{"type": "retraction", "DOI": "10.1/x"}]}}
        self.assertEqual(merge_crossref_status("none", payload), "retracted")

    def test_retracted_claim_scores_low(self):
        doc = Document(doc_id="PMID:1", title="t", abstract="a", pubtypes=["Retracted Publication"])
        doc.retraction_status = "retracted"
        claim = Claim(doc_id="PMID:1", entity_id="NP:OXT", direction="increase", sample_size=500)
        points, band, reasons = rubric(doc, claim, "rct")
        self.assertEqual(band, "low")
        self.assertTrue(any("RETRACTED" in r for r in reasons))

    def test_contradiction_tally(self):
        rows = [
            {"entity_id": "NP:OXT", "outcome": "anxiety", "direction": "increase", "doc_id": "a"},
            {"entity_id": "NP:OXT", "outcome": "anxiety", "direction": "decrease", "doc_id": "b"},
        ]
        tally = consistency(rows)
        self.assertTrue(tally[("NP:OXT", "anxiety")]["contradiction"])


class TestStoreAndPipeline(unittest.TestCase):
    def _corpus(self):
        return {
            "meta": {"note": "synthetic test corpus"},
            "results": [
                {
                    "id": "1",
                    "pmid": "1001",
                    "title": "Intranasal oxytocin and social cognition",
                    "abstractText": (
                        "Intranasal oxytocin improved social cognition in 120 participants "
                        "(p = 0.01) in a randomized placebo-controlled trial. "
                        "Oxytocin did not change anxiety scores."
                    ),
                    "journalTitle": "Test Journal",
                    "pubYear": "2024",
                    "pubTypeList": {"pubType": ["Randomized Controlled Trial"]},
                    "doi": "10.1000/test1",
                },
                {
                    "id": "2",
                    "pmid": "1002",
                    "title": "BDNF and memory",
                    "abstractText": "BDNF levels were lower in patients with memory impairment.",
                    "journalTitle": "Test Journal",
                    "pubYear": "2023",
                    "pubTypeList": {"pubType": ["Journal Article"]},
                },
            ],
            "annotations": {},
        }

    def test_end_to_end_and_provenance(self):
        from ntx.ingest import doc_from_epmc

        corpus = self._corpus()
        docs = [doc_from_epmc(r) for r in corpus["results"]]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                stats = process_documents(store, docs, corpus["annotations"])
                self.assertEqual(stats["documents"], 2)
                self.assertGreater(stats["claims"], 1)
                self.assertEqual(stats["invalid_spans"], 0)
                self.assertEqual(store.provenance_errors(), [])
                counts = store.counts()
                self.assertEqual(counts["documents"], 2)
                self.assertEqual(counts["claims"], stats["claims"])
            finally:
                store.close()

    def test_retraction_metadata_applied(self):
        from ntx.ingest import apply_pubmed, doc_from_epmc

        doc = doc_from_epmc({"id": "3", "pmid": "1003", "title": "x", "abstractText": "y"})
        apply_pubmed(doc, {"pubtype": ["Retracted Publication"], "articleids": []})
        self.assertEqual(doc.retraction_status, "retracted")
        self.assertEqual(retraction_from_pubtypes(doc.pubtypes), "retracted")

    def test_v6_to_v7_migration_preserves_documents(self):
        from ntx.ingest import doc_from_epmc

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            store = Store(db)
            try:
                store.add_document(doc_from_epmc({"id": "1", "pmid": "1", "title": "T", "abstractText": "A"}))
            finally:
                store.close()
            conn = sqlite3.connect(str(db))
            try:
                conn.executescript("DROP TABLE fulltext_claims; DROP TABLE document_texts;")
                conn.execute("PRAGMA user_version=6")
                conn.commit()
            finally:
                conn.close()
            upgraded = Store(db)
            try:
                self.assertEqual(upgraded.schema_version, SCHEMA_VERSION)
                self.assertEqual(upgraded.counts()["documents"], 1)
                self.assertEqual(upgraded.counts()["fulltext_documents"], 0)
                self.assertEqual(upgraded.counts()["fulltext_claims"], 0)
            finally:
                upgraded.close()

    def test_fulltext_snapshot_and_claim_provenance(self):
        text = "Oxytocin increased trust."
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                store.add_document_text(
                    doc_id="PMID:1",
                    pmcid="PMC1",
                    source_url="https://example.org/PMC1/fullTextXML",
                    text=text,
                    sections=[{"name": "results", "start": 0}],
                    content_hash=digest,
                    fetched_at="2026-01-01T00:00:00+00:00",
                    license="cc by",
                )
                claim = Claim(
                    doc_id="PMID:1",
                    entity_id="NP:OXT",
                    direction="increase",
                    outcome="trust",
                    sentence=text,
                    start=0,
                    end=len(text),
                    section="results",
                )
                self.assertEqual(store.add_fulltext_claims([claim], text_hash=digest), 1)
                self.assertEqual(store.add_fulltext_claims([claim], text_hash=digest), 0)
                self.assertEqual(store.counts()["fulltext_documents"], 1)
                self.assertEqual(store.counts()["fulltext_claims"], 1)
                stored = store.get_document_text("PMID:1")
                self.assertIsNotNone(stored)
                self.assertEqual(stored["sections"], [{"name": "results", "start": 0}])
                self.assertEqual(store.fulltext_provenance_errors(), [])
                store.conn.execute(
                    "UPDATE document_texts SET content_hash = ? WHERE doc_id = 'PMID:1'", ("0" * 64,)
                )
                store.conn.commit()
                errors = store.fulltext_provenance_errors()
                self.assertEqual(errors[0]["reason"], "text hash mismatch")
                self.assertEqual(errors[0]["source"], "fulltext")
            finally:
                store.close()

    def test_eval_harness_scores_a_known_db(self):
        from ntx.eval import evaluate, load_gold
        from ntx.ingest import doc_from_epmc

        corpus = self._corpus()
        docs = [doc_from_epmc(r) for r in corpus["results"]]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                process_documents(store, docs, corpus["annotations"])
                gold_path = Path(tmp) / "gold.jsonl"
                claims = [
                    {
                        "entity_id": row["entity_id"],
                        "direction": row["direction"],
                        "outcome": row["outcome"],
                        "start": row["start"],
                        "end": row["end"],
                        "sentence": row["sentence"],
                    }
                    for row in store.claims()
                    if row["doc_id"] == "PMID:1001"
                ]
                mentions = [
                    {"start": row["start"], "end": row["end"], "entity_id": row["entity_id"]}
                    for row in store.conn.execute(
                        "SELECT * FROM mentions WHERE accepted=1 AND doc_id='PMID:1001'"
                    )
                ]
                gold_path.write_text(
                    json.dumps({"doc_id": "PMID:1001", "mentions": mentions, "claims": claims}) + "\n",
                    encoding="utf-8",
                )
                result = evaluate(store, load_gold(gold_path))
                self.assertEqual(result["mentions_id"]["f1"], 1.0)
                self.assertEqual(result["claims_strict"]["f1"], 1.0)
                self.assertEqual(result["provenance_errors"], 0)
            finally:
                store.close()


class TestDiscovery(unittest.TestCase):
    def _store(self, tmp):
        store = Store(Path(tmp) / "d.db")
        claims = [
            Claim(doc_id="PMID:1", entity_id="NP:OXT", direction="increase", outcome="anxiety", design="rct"),
            Claim(doc_id="PMID:2", entity_id="NP:OXT", direction="decrease", outcome="anxiety", design="rct"),
            Claim(
                doc_id="PMID:3", entity_id="NTF:BDNF", direction="increase", outcome="memory", design="animal"
            ),
        ]
        store.add_claims(claims)
        return store

    def test_contradiction_and_weak_evidence(self):
        from ntx import discovery

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                hypotheses = discovery.generate(store, top=50)
                kinds = {(h.kind, h.entity_id, h.outcome) for h in hypotheses}
                self.assertIn(("contradiction", "NP:OXT", "anxiety"), kinds)
                self.assertIn(("weak_evidence", "NTF:BDNF", "memory"), kinds)
                self.assertIn(("mechanism_gap", "NP:OXT", "neuroplasticity"), kinds)
                text = discovery.format_report(hypotheses)
                self.assertIn("Ranked discovery hypotheses", text)
                self.assertIn("H-", text)
            finally:
                store.close()

    def test_every_hypothesis_cites_evidence(self):
        from ntx import discovery

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                for hypothesis in discovery.generate(store, top=50):
                    self.assertTrue(hypothesis.doc_ids)
                    self.assertTrue(hypothesis.assay)
                    self.assertTrue(hypothesis.design)
            finally:
                store.close()


class TestPackagingAndReproducibility(unittest.TestCase):
    def test_bundled_resources_are_available(self):
        data = config.ontology()
        self.assertGreaterEqual(len(data["entities"]), 30)
        self.assertTrue(config.queries()["templates"])
        self.assertGreater(len(config.read_resource("demo_corpus.json")), 1024)

    def test_manifest_identity_ignores_creation_time(self):
        fields = {
            "command": "demo",
            "code_version": "0.2.0",
            "git_sha": "abc",
            "git_dirty": False,
            "python": "3.13.5",
            "platform": "test",
            "config_hash": "c",
            "corpus_hash": "p",
            "schema_version": SCHEMA_VERSION,
        }
        first = manifest.Manifest(created="2024-01-01T00:00:00+00:00", **fields)
        second = manifest.Manifest(created="2025-06-01T00:00:00+00:00", **fields)
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.content_hash(), second.content_hash())
        self.assertNotEqual(first.to_json(), second.to_json())

    def test_manifest_roundtrip_in_store(self):
        built = manifest.build("test", corpus_hash="deadbeef")
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.db")
            try:
                store.add_manifest(built)
                rows = store.manifests()
                self.assertEqual(len(rows), 1)
                payload = json.loads(rows[0]["payload"])
                self.assertEqual(payload["run_id"], built.run_id)
                self.assertEqual(payload["corpus_hash"], "deadbeef")
            finally:
                store.close()

    def test_schema_migrates_from_version_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v1.db"
            store = Store(path)
            store.conn.execute("DROP TABLE manifests")
            store.conn.execute("PRAGMA user_version=1")
            store.conn.commit()
            store.close()

            upgraded = Store(path)
            try:
                self.assertEqual(upgraded.schema_version, SCHEMA_VERSION)
                tables = {
                    row[0]
                    for row in upgraded.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertIn("manifests", tables)
            finally:
                upgraded.close()

    def test_frozen_clock_is_used_verbatim(self):
        with mock.patch.dict(os.environ, {"NTX_FROZEN_TIME": "2024-01-02T03:04:05+00:00"}):
            self.assertEqual(clock.iso(), "2024-01-02T03:04:05+00:00")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, size=-1):
        return self._payload if size is None or size < 0 else self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeTextResponse:
    def __init__(self, text):
        self._payload = text.encode("utf-8")

    def read(self, size=-1):
        return self._payload if size is None or size < 0 else self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class TestHttpCache(unittest.TestCase):
    def test_cached_response_is_available_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with mock.patch(
                "ntx.http.urllib.request.urlopen", return_value=_FakeResponse({"ok": 1})
            ) as opener:
                self.assertEqual(http_mod.get_json("https://example.org/a", cache), {"ok": 1})
            self.assertEqual(opener.call_count, 1)
            with mock.patch("ntx.http.urllib.request.urlopen", side_effect=AssertionError("network used")):
                self.assertEqual(http_mod.get_json("https://example.org/a", cache, offline=True), {"ok": 1})

    def test_offline_cache_miss_raises(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(http_mod.OfflineError):
            http_mod.get_json("https://example.org/missing", Path(tmp), offline=True)

    def test_non_https_urls_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            http_mod.get_json("http://example.org/a", Path(tmp))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            http_mod.get_json("file:///etc/passwd", Path(tmp))

    def test_oversized_response_is_rejected(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("ntx.http.urllib.request.urlopen", return_value=_FakeResponse({"ok": 1})),
            self.assertRaises(ValueError),
        ):
            http_mod.get_json("https://example.org/big", Path(tmp), max_bytes=4)

    def test_transient_network_error_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            responses = [urllib.error.URLError("boom"), _FakeResponse({"ok": 2})]
            with (
                mock.patch("ntx.http.urllib.request.urlopen", side_effect=responses),
                mock.patch("ntx.http.time.sleep"),
            ):
                self.assertEqual(http_mod.get_json("https://example.org/retry", Path(tmp)), {"ok": 2})

    def test_permanent_failure_raises_runtime_error(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("ntx.http.urllib.request.urlopen", side_effect=urllib.error.URLError("down")),
            mock.patch("ntx.http.time.sleep"),
            self.assertRaises(RuntimeError),
        ):
            http_mod.get_json("https://example.org/fail", Path(tmp), retries=2)

    def test_text_response_is_cached_and_available_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with mock.patch(
                "ntx.http.urllib.request.urlopen", return_value=_FakeTextResponse("<article/>")
            ) as opener:
                self.assertEqual(http_mod.get_text("https://example.org/a.xml", cache), "<article/>")
            self.assertEqual(opener.call_count, 1)
            self.assertTrue(any(path.suffix == ".txt" for path in cache.iterdir()))
            with mock.patch("ntx.http.urllib.request.urlopen", side_effect=AssertionError("network used")):
                self.assertEqual(
                    http_mod.get_text("https://example.org/a.xml", cache, offline=True), "<article/>"
                )

    def test_text_offline_cache_miss_raises(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(http_mod.OfflineError):
            http_mod.get_text("https://example.org/missing.xml", Path(tmp), offline=True)

    def test_text_response_size_limit_is_enforced(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("ntx.http.urllib.request.urlopen", return_value=_FakeTextResponse("12345")),
            self.assertRaises(ValueError),
        ):
            http_mod.get_text("https://example.org/big.xml", Path(tmp), max_bytes=4)


class TestFulltextParsing(unittest.TestCase):
    JATS = """<?xml version="1.0"?>
    <article>
      <front><article-meta><title-group><article-title>An oxytocin trial</article-title>
      </title-group></article-meta></front>
      <body>
        <sec><title>Methods</title><p>We gave <italic>oxytocin</italic> to 40 adults.</p></sec>
        <sec><title>Results</title>
          <p>Oxytocin increased trust.</p>
          <p>It also reduced fear.</p>
          <sec><title>Subgroup</title><p>No effect was seen in men.</p></sec>
        </sec>
        <sec><title>Discussion</title><p>The result is preliminary.</p></sec>
      </body>
    </article>"""

    def test_offsets_and_sections_are_exact(self):
        parsed = fulltext_mod.parse_jats(self.JATS)
        self.assertEqual(parsed.title, "An oxytocin trial")
        self.assertEqual([name for _, name in parsed.sections], ["methods", "results", "discussion"])
        self.assertEqual(
            parsed.text[parsed.sections[0][0] :].split("\n\n")[0], "We gave oxytocin to 40 adults."
        )
        self.assertEqual(
            parsed.text[parsed.sections[1][0] : parsed.sections[2][0]],
            "Oxytocin increased trust.\n\nIt also reduced fear.\n\nNo effect was seen in men.\n\n",
        )
        self.assertEqual(parsed.text.count("No effect was seen in men."), 1)
        self.assertNotIn("<italic>", parsed.text)
        self.assertTrue(parsed.text[parsed.sections[1][0] :].startswith("Oxytocin increased trust."))

    def test_doctype_and_entity_are_rejected_before_parse(self):
        for payload in ("<!DOCTYPE article><article/>", "<article><!ENTITY x 'y'></article>"):
            with self.assertRaises(fulltext_mod.FulltextError) as context:
                fulltext_mod.parse_jats(payload)
            self.assertEqual(context.exception.status, "unsafe_xml")

    def test_invalid_and_empty_documents_have_stable_statuses(self):
        with self.assertRaises(fulltext_mod.FulltextError) as invalid:
            fulltext_mod.parse_jats("<article>")
        self.assertEqual(invalid.exception.status, "invalid_xml")
        with self.assertRaises(fulltext_mod.FulltextError) as empty:
            fulltext_mod.parse_jats("<article><body><sec><title>Results</title></sec></body></article>")
        self.assertEqual(empty.exception.status, "empty")
        with self.assertRaises(fulltext_mod.FulltextError) as missing:
            fulltext_mod.parse_jats("<article><front/></article>")
        self.assertEqual(missing.exception.status, "empty")

    def test_oversized_xml_and_text_are_rejected(self):
        with (
            mock.patch.object(fulltext_mod, "MAX_XML_CHARS", 10),
            self.assertRaises(fulltext_mod.FulltextError) as context,
        ):
            fulltext_mod.parse_jats(self.JATS)
        self.assertEqual(context.exception.status, "too_large")
        with (
            mock.patch.object(fulltext_mod, "MAX_FULLTEXT_CHARS", 5),
            self.assertRaises(fulltext_mod.FulltextError) as context,
        ):
            fulltext_mod.parse_jats(self.JATS)
        self.assertEqual(context.exception.status, "too_large")

    def test_caption_text_is_kept(self):
        xml = (
            "<article><body><sec><title>Results</title><p>Main text.</p>"
            "<table-wrap><caption>Table 1. Means by group.</caption></table-wrap>"
            "</sec></body></article>"
        )
        parsed = fulltext_mod.parse_jats(xml)
        self.assertIn("Table 1. Means by group.", parsed.text)


class TestFulltextLayer(unittest.TestCase):
    JATS = (
        "<article><front><article-meta><title-group><article-title>Oxytocin and trust</article-title>"
        "</title-group></article-meta></front><body>"
        "<sec><title>Results</title><p>Oxytocin increased trust in a randomized trial.</p></sec>"
        "<sec><title>Discussion</title><p>The finding is preliminary.</p></sec>"
        "</body></article>"
    )

    def _doc(self):
        from ntx.ingest import doc_from_epmc

        return doc_from_epmc(
            {
                "id": "1",
                "pmid": "1001",
                "pmcid": "PMC123",
                "title": "Oxytocin and trust",
                "abstractText": "Oxytocin increased trust.",
                "journalTitle": "Test Journal",
                "pubYear": "2024",
                "pubTypeList": {"pubType": ["Randomized Controlled Trial"]},
                "license": "cc by",
            }
        )

    def _store(self, tmp: str):
        store = Store(Path(tmp) / "t.db")
        store.add_document(self._doc())
        return store

    def test_fetch_caches_and_stores_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            cache = Path(tmp) / "cache"
            try:
                with mock.patch(
                    "ntx.http.urllib.request.urlopen", return_value=_FakeTextResponse(self.JATS)
                ) as opener:
                    payload = fulltext_mod.fetch_fulltext(store, cache_dir=cache)
                self.assertEqual(opener.call_count, 1)
                self.assertEqual(payload["counts"]["fetched"], 1)
                stored = store.get_document_text("PMID:1001")
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored["pmcid"], "PMC123")
                self.assertEqual(stored["license"], "cc by")
                self.assertEqual(stored["sections"][0]["name"], "results")
                self.assertEqual(stored["text"][:24], "Oxytocin increased trust")
                self.assertEqual(store.counts()["fulltext_documents"], 1)
                second = fulltext_mod.fetch_fulltext(store, cache_dir=cache, offline=True)
                self.assertEqual(second["counts"]["already_present"], 1)
            finally:
                store.close()

    def test_fetch_reports_missing_invalid_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                no_pmcid = self._doc()
                no_pmcid.doc_id = "PMID:missing"
                no_pmcid.pmcid = None
                store.add_document(no_pmcid)
                invalid = self._doc()
                invalid.doc_id = "PMID:invalid"
                invalid.pmcid = "not-a-pmcid"
                store.add_document(invalid)
                payload = fulltext_mod.fetch_fulltext(store, cache_dir=Path(tmp) / "cache", offline=True)
                self.assertEqual(payload["counts"]["no_pmcid"], 1)
                self.assertEqual(payload["counts"]["invalid_pmcid"], 1)
                self.assertEqual(payload["counts"]["unavailable"], 0)
                missing = fulltext_mod.fetch_fulltext(
                    store, doc_id="PMID:absent", cache_dir=Path(tmp) / "cache", offline=True
                )
                self.assertEqual(missing["counts"]["not_found"], 1)
            finally:
                store.close()

    def test_extract_keeps_results_and_exact_spans(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                parsed = fulltext_mod.parse_jats(self.JATS)
                digest = hashlib.sha256(parsed.text.encode("utf-8")).hexdigest()
                store.add_document_text(
                    doc_id="PMID:1001",
                    pmcid="PMC123",
                    source_url="https://example.org/PMC123/fullTextXML",
                    text=parsed.text,
                    sections=[{"name": name, "start": start} for start, name in parsed.sections],
                    content_hash=digest,
                    fetched_at="2026-01-01T00:00:00+00:00",
                )
                first = fulltext_mod.extract_fulltext(store)
                self.assertEqual(first["counts"]["documents"], 1)
                self.assertGreaterEqual(first["counts"]["claims"], 1)
                claims = store.fulltext_claims("PMID:1001")
                self.assertTrue(claims)
                for row in claims:
                    self.assertEqual(parsed.text[row["start"] : row["end"]], row["sentence"])
                    self.assertIn(row["section"], {"results", "discussion", "conclusions"})
                second = fulltext_mod.extract_fulltext(store)
                self.assertEqual(second["counts"]["skipped_existing"], first["counts"]["claims"])
                self.assertEqual(store.fulltext_provenance_errors(), [])
            finally:
                store.close()

    def test_report_corroborates_and_hashes_without_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                doc = self._doc()
                abstract = doc.text()
                sentence = "Oxytocin increased trust."
                start = abstract.find(sentence)
                store.add_claims(
                    [
                        Claim(
                            doc_id="PMID:1001",
                            entity_id="NP:OXT",
                            direction="increase",
                            outcome="social_cognition",
                            sentence=sentence,
                            start=start,
                            end=start + len(sentence),
                        )
                    ]
                )
                parsed = fulltext_mod.parse_jats(self.JATS)
                store.add_document_text(
                    doc_id="PMID:1001",
                    pmcid="PMC123",
                    source_url="https://example.org/PMC123/fullTextXML",
                    text=parsed.text,
                    sections=[{"name": name, "start": start} for start, name in parsed.sections],
                    content_hash=hashlib.sha256(parsed.text.encode("utf-8")).hexdigest(),
                    fetched_at="2026-01-01T00:00:00+00:00",
                )
                fulltext_mod.extract_fulltext(store)
                with mock.patch.object(fulltext_mod.clock, "iso", side_effect=["2026-01-01", "2026-01-02"]):
                    first = fulltext_mod.build_report(store)
                    second = fulltext_mod.build_report(store)
                self.assertEqual(first["counts"]["corroborated"], 1)
                self.assertEqual(first["content_hash"], second["content_hash"])
                self.assertIn("results", first["section_counts"])
                self.assertIn("Full-text reconciliation", fulltext_mod.format_report(first))
            finally:
                store.close()

    def test_fetch_maps_parser_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                for index in (1, 2):
                    doc = self._doc()
                    doc.doc_id = "PMID:%d" % index
                    store.add_document(doc)
                with mock.patch(
                    "ntx.fulltext.http.get_text",
                    side_effect=["<!DOCTYPE article><article/>", "<article><body/></article>"],
                ):
                    payload = fulltext_mod.fetch_fulltext(store, cache_dir=Path(tmp) / "cache")
                self.assertEqual(payload["counts"]["unsafe_xml"], 1)
                self.assertEqual(payload["counts"]["empty"], 1)
                self.assertEqual(payload["counts"]["fetched"], 0)
            finally:
                store.close()

    def test_pmcid_normalization(self):
        self.assertEqual(fulltext_mod.normalize_pmcid(" pmc123 "), "PMC123")
        self.assertEqual(fulltext_mod.normalize_pmcid("PMC001"), "PMC001")
        self.assertEqual(fulltext_mod.normalize_pmcid("123"), "")
        self.assertEqual(fulltext_mod.normalize_pmcid(None), "")

    def test_cli_report_writes_json_and_fails_on_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            store = self._store(tmp)
            try:
                doc = self._doc()
                abstract = doc.text()
                sentence = "Oxytocin increased trust."
                start = abstract.find(sentence)
                store.add_claims(
                    [
                        Claim(
                            doc_id="PMID:1001",
                            entity_id="NP:OXT",
                            direction="increase",
                            outcome="social_cognition",
                            sentence=sentence,
                            start=start,
                            end=start + len(sentence),
                        )
                    ]
                )
                text = "Oxytocin decreased trust."
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                store.add_document_text(
                    doc_id="PMID:1001",
                    pmcid="PMC123",
                    source_url="https://example.org/PMC123/fullTextXML",
                    text=text,
                    sections=[{"name": "results", "start": 0}],
                    content_hash=digest,
                    fetched_at="2026-01-01T00:00:00+00:00",
                )
                store.add_fulltext_claims(
                    [
                        Claim(
                            doc_id="PMID:1001",
                            entity_id="NP:OXT",
                            direction="decrease",
                            outcome="social_cognition",
                            sentence=text,
                            start=0,
                            end=len(text),
                            section="results",
                        )
                    ],
                    text_hash=digest,
                )
            finally:
                store.close()
            out = Path(tmp) / "reports" / "fulltext.json"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["fulltext", "report", "--db", str(db), "--json", "--out", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["direction_conflict"], 1)
            self.assertEqual(json.loads(buffer.getvalue())["schema"], fulltext_mod.REPORT_SCHEMA)
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli_mod.main(["fulltext", "report", "--db", str(db), "--fail-on-conflict"])
            self.assertEqual(code, 1)

    def test_cli_extract_and_check_fulltext_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            store = self._store(tmp)
            try:
                parsed = fulltext_mod.parse_jats(self.JATS)
                store.add_document_text(
                    doc_id="PMID:1001",
                    pmcid="PMC123",
                    source_url="https://example.org/PMC123/fullTextXML",
                    text=parsed.text,
                    sections=[{"name": name, "start": start} for start, name in parsed.sections],
                    content_hash=hashlib.sha256(parsed.text.encode("utf-8")).hexdigest(),
                    fetched_at="2026-01-01T00:00:00+00:00",
                )
            finally:
                store.close()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["fulltext", "extract", "--db", str(db), "--json"])
            self.assertEqual(code, 0)
            self.assertGreaterEqual(json.loads(buffer.getvalue())["counts"]["claims"], 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli_mod.main(["check", "--db", str(db)]), 0)
            store = Store(db)
            try:
                store.conn.execute("UPDATE fulltext_claims SET sentence = 'tampered' WHERE claim_id = 1")
                store.conn.commit()
            finally:
                store.close()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["check", "--db", str(db), "--json"])
            self.assertEqual(code, 1)
            self.assertEqual(len(json.loads(buffer.getvalue())["fulltext_provenance_errors"]), 1)


class TestReconciliation(unittest.TestCase):
    def _setup(
        self,
        tmp: str,
        *,
        abstract_sentence: str = "Oxytocin increased trust.",
        fulltext_sentence: str = "Oxytocin increased trust.",
        abstract_direction: str = "increase",
        fulltext_direction: str = "increase",
        abstract_p: float | None = None,
        fulltext_p: float | None = None,
        abstract_n: int | None = None,
        fulltext_n: int | None = None,
        abstract_hedged: bool = False,
        fulltext_hedged: bool = False,
        abstract_quant: dict[str, Any] | None = None,
        fulltext_quant: dict[str, Any] | None = None,
        auto_quant: bool = False,
        fulltext_section: str = "results",
        with_abstract: bool = True,
        with_fulltext_claim: bool = True,
        fulltext_text: str | None = None,
        second_fulltext: tuple[str, str] | None = None,
        entity_id: str = "NP:OXT",
    ):
        from ntx.ingest import doc_from_epmc

        store = Store(Path(tmp) / "t.db")
        doc = doc_from_epmc(
            {
                "id": "1",
                "pmid": "1001",
                "pmcid": "PMC123",
                "title": "Oxytocin and trust",
                "abstractText": abstract_sentence,
                "journalTitle": "Test Journal",
                "pubYear": "2024",
                "pubTypeList": {"pubType": ["Randomized Controlled Trial"]},
                "license": "cc by",
            }
        )
        store.add_document(doc)
        if with_abstract:
            abstract_text = doc.text()
            start = abstract_text.find(abstract_sentence)
            store.add_claims(
                [
                    Claim(
                        doc_id=doc.doc_id,
                        entity_id=entity_id,
                        direction=abstract_direction,
                        outcome="social_cognition",
                        sentence=abstract_sentence,
                        start=start,
                        end=start + len(abstract_sentence),
                        sample_size=abstract_n,
                        p_value=abstract_p,
                        hedged=abstract_hedged,
                        quant=(
                            abstract_quant
                            if abstract_quant is not None
                            else (quant_mod.extract_quant(abstract_sentence, start) if auto_quant else {})
                        ),
                    )
                ]
            )
        text = fulltext_text if fulltext_text is not None else fulltext_sentence
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        store.add_document_text(
            doc_id=doc.doc_id,
            pmcid="PMC123",
            source_url="https://example.org/PMC123/fullTextXML",
            text=text,
            sections=[{"name": fulltext_section, "start": 0}],
            content_hash=digest,
            fetched_at="2026-01-01T00:00:00+00:00",
        )
        if with_fulltext_claim:
            start = text.find(fulltext_sentence)
            claims = [
                Claim(
                    doc_id=doc.doc_id,
                    entity_id=entity_id,
                    direction=fulltext_direction,
                    outcome="social_cognition",
                    sentence=fulltext_sentence,
                    start=start,
                    end=start + len(fulltext_sentence),
                    sample_size=fulltext_n,
                    p_value=fulltext_p,
                    hedged=fulltext_hedged,
                    quant=(
                        fulltext_quant
                        if fulltext_quant is not None
                        else (quant_mod.extract_quant(fulltext_sentence, start) if auto_quant else {})
                    ),
                    section=fulltext_section,
                )
            ]
            if second_fulltext:
                sentence, direction = second_fulltext
                start = text.find(sentence)
                claims.append(
                    Claim(
                        doc_id=doc.doc_id,
                        entity_id=entity_id,
                        direction=direction,
                        outcome="social_cognition",
                        sentence=sentence,
                        start=start,
                        end=start + len(sentence),
                        section=fulltext_section,
                    )
                )
            store.add_fulltext_claims(claims, text_hash=digest)
        return store

    def _finding(self, store, index: int = 0) -> dict[str, Any]:
        payload = reconcile_mod.build_report(store)
        return payload["findings"][index]

    def test_status_matrix(self):
        cases = [
            ({"abstract_direction": "increase", "fulltext_direction": "increase"}, "corroborated"),
            ({"abstract_direction": "increase", "fulltext_direction": "decrease"}, "direction_conflict"),
            ({"with_fulltext_claim": False, "fulltext_text": "Different body."}, "abstract_only"),
            ({"with_abstract": False, "fulltext_direction": "increase"}, "fulltext_only"),
            ({"abstract_direction": "mixed", "fulltext_direction": "increase"}, "indeterminate"),
        ]
        for options, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                store = self._setup(tmp, **options)
                try:
                    finding = self._finding(store)
                    self.assertEqual(finding["status"], expected)
                finally:
                    store.close()

    def test_significance_sample_and_hedging_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(
                tmp,
                abstract_p=0.01,
                fulltext_p=0.4,
                abstract_n=100,
                fulltext_n=20,
                fulltext_hedged=True,
            )
            try:
                finding = self._finding(store)
                self.assertEqual(finding["status"], "corroborated")
                self.assertIn("significance_conflict", finding["reasons"])
                self.assertIn("sample_size_discrepancy", finding["reasons"])
                self.assertIn("hedging_mismatch", finding["reasons"])
                self.assertEqual(finding["severity"], "high")
            finally:
                store.close()

    def test_missing_values_do_not_emit_numeric_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(tmp)
            try:
                finding = self._finding(store)
                self.assertEqual(finding["reasons"], [])
            finally:
                store.close()

    def test_discussion_only_and_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(tmp, fulltext_section="discussion")
            try:
                finding = self._finding(store)
                self.assertEqual(finding["status"], "corroborated")
                self.assertIn("discussion_only", finding["reasons"])
                self.assertEqual(finding["severity"], "low")
            finally:
                store.close()
        with tempfile.TemporaryDirectory() as tmp:
            sentence = "Oxytocin increased trust in a long randomized trial of adults."
            store = self._setup(
                tmp,
                abstract_sentence=sentence,
                fulltext_text=sentence,
                with_fulltext_claim=False,
            )
            try:
                finding = self._finding(store)
                self.assertEqual(finding["status"], "abstract_only")
                self.assertIn("abstract_sentence_in_fulltext", finding["reasons"])
                self.assertEqual(len(finding["echo_claim_ids"]), 1)
            finally:
                store.close()

    def test_mixed_directions_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(
                tmp,
                fulltext_text="Oxytocin increased trust. Oxytocin decreased trust.",
                second_fulltext=("Oxytocin decreased trust.", "decrease"),
            )
            try:
                payload = reconcile_mod.build_report(store)
                finding = payload["findings"][0]
                self.assertEqual(finding["status"], "corroborated")
                self.assertIn("mixed_directions", finding["reasons"])
                self.assertLessEqual(len(finding["fulltext_claims"]), reconcile_mod.MAX_REFS)
                grouped = reconcile_mod.by_entity(payload)
                self.assertEqual(grouped["NP:OXT"]["counts"]["mixed_directions"], 1)
            finally:
                store.close()

    def test_effect_size_discrepancy_is_medium(self):
        abstract = "Oxytocin increased trust (Cohen's d = 0.20, 95% CI 0.00 to 0.40)."
        fulltext = "Oxytocin increased trust (Cohen's d = 0.90, 95% CI 0.60 to 1.20)."
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(
                tmp,
                abstract_sentence=abstract,
                fulltext_sentence=fulltext,
                auto_quant=True,
            )
            try:
                finding = self._finding(store)
                self.assertEqual(finding["status"], "corroborated")
                self.assertIn("effect_size_discrepancy", finding["reasons"])
                self.assertEqual(finding["severity"], "medium")
                self.assertFalse(finding["effect_comparison"]["ci_overlap"])
                self.assertEqual(finding["effect_comparison"]["abstract"]["type"], "smd")
            finally:
                store.close()

    def test_overlapping_ci_is_not_a_discrepancy(self):
        abstract = "Oxytocin increased trust (Cohen's d = 0.40, 95% CI 0.10 to 0.70)."
        fulltext = "Oxytocin increased trust (Cohen's d = 0.55, 95% CI 0.30 to 0.80)."
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(
                tmp,
                abstract_sentence=abstract,
                fulltext_sentence=fulltext,
                auto_quant=True,
            )
            try:
                finding = self._finding(store)
                self.assertEqual(finding["status"], "corroborated")
                self.assertNotIn("effect_size_discrepancy", finding["reasons"])
                self.assertTrue(finding["effect_comparison"]["ci_overlap"])
                self.assertEqual(finding["severity"], "info")
            finally:
                store.close()

    def test_effect_type_and_comparator_mismatches(self):
        abstract = "Oxytocin increased trust compared with placebo (Cohen's d = 0.40, 95% CI 0.10 to 0.70)."
        fulltext = "Oxytocin increased trust versus no treatment (OR = 1.40, 95% CI 1.10 to 1.80)."
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(
                tmp,
                abstract_sentence=abstract,
                fulltext_sentence=fulltext,
                auto_quant=True,
            )
            try:
                finding = self._finding(store)
                self.assertEqual(finding["effect_comparison"]["status"], "type_mismatch")
                self.assertIn("effect_type_mismatch", finding["reasons"])
                self.assertIn("comparator_mismatch", finding["reasons"])
                self.assertEqual(finding["severity"], "low")
            finally:
                store.close()

    def test_multiple_effects_are_ambiguous_not_a_discrepancy(self):
        abstract = "Oxytocin increased trust (OR = 1.40, 95% CI 1.10 to 1.80; RR = 1.20)."
        fulltext = "Oxytocin increased trust (OR = 1.40, 95% CI 1.10 to 1.80)."
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(
                tmp,
                abstract_sentence=abstract,
                fulltext_sentence=fulltext,
                auto_quant=True,
            )
            try:
                finding = self._finding(store)
                comparison = finding["effect_comparison"]
                self.assertEqual(comparison["status"], "ambiguous")
                self.assertEqual(len(comparison["abstract_effects"]), 2)
                self.assertIn("effect_ambiguous", finding["reasons"])
                self.assertNotIn("effect_size_discrepancy", finding["reasons"])
                self.assertEqual(finding["severity"], "low")
            finally:
                store.close()

    def test_dose_route_mismatch(self):
        abstract = "Oxytocin increased trust compared with placebo at 10 mg/kg (Cohen's d = 0.40)."
        fulltext = "Oxytocin increased trust compared with placebo at 20 mg/kg (Cohen's d = 0.40)."
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(
                tmp,
                abstract_sentence=abstract,
                fulltext_sentence=fulltext,
                auto_quant=True,
            )
            try:
                finding = self._finding(store)
                self.assertIn("dose_route_mismatch", finding["reasons"])
                self.assertEqual(finding["severity"], "low")
            finally:
                store.close()

    def test_report_is_deterministic_and_entity_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(tmp)
            try:
                with mock.patch.object(reconcile_mod.clock, "iso", side_effect=["a", "b"]):
                    first = reconcile_mod.build_report(store)
                    second = reconcile_mod.build_report(store)
                self.assertEqual(first["content_hash"], second["content_hash"])
                self.assertEqual(first["schema"], "ntx-fulltext-report-v4")
                self.assertEqual(first["counts"]["groups"], 1)
                filtered = reconcile_mod.build_report(store, entity_id="NP:OTHER")
                self.assertEqual(filtered["counts"]["groups"], 0)
                self.assertEqual(filtered["findings"], [])
            finally:
                store.close()

    def test_cli_severity_thresholds_and_conflict_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(tmp, abstract_direction="increase", fulltext_direction="decrease")
            db = store.path
            store.close()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    cli_mod.main(["fulltext", "report", "--db", str(db), "--fail-on", "high"]), 1
                )
                self.assertEqual(
                    cli_mod.main(["fulltext", "report", "--db", str(db), "--fail-on", "medium"]), 1
                )
                self.assertEqual(
                    cli_mod.main(["fulltext", "report", "--db", str(db), "--fail-on", "none"]), 0
                )
                self.assertEqual(
                    cli_mod.main(["fulltext", "report", "--db", str(db), "--fail-on-conflict"]), 1
                )
                self.assertEqual(
                    cli_mod.main(["fulltext", "report", "--db", str(db), "--entity", "NP:OTHER", "--json"]),
                    0,
                )

    def test_report_with_fulltext_and_default_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(tmp)
            try:
                default_dir = Path(tmp) / "default"
                full_dir = Path(tmp) / "full"
                report_mod.render_all(store, default_dir)
                report_mod.render_all(store, full_dir, include_fulltext=True)
                self.assertNotIn(
                    "Full-text reconciliation",
                    (default_dir / "oxt.md").read_text(encoding="utf-8"),
                )
                self.assertIn(
                    "Full-text reconciliation",
                    (full_dir / "oxt.md").read_text(encoding="utf-8"),
                )
                self.assertIn(
                    "Full-text reconciliation",
                    (full_dir / "oxt.html").read_text(encoding="utf-8"),
                )
            finally:
                store.close()

    def test_report_with_fulltext_escapes_quotes_and_rejects_bad_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = "<script>alert(1)</script> Oxytocin increased trust."
            store = self._setup(tmp, fulltext_sentence=script)
            try:
                out = Path(tmp) / "full"
                report_mod.render_all(store, out, include_fulltext=True)
                self.assertNotIn("<script>alert(1)</script>", (out / "oxt.html").read_text(encoding="utf-8"))
                store.conn.execute("UPDATE fulltext_claims SET sentence = 'tampered'")
                store.conn.commit()
                with self.assertRaises(RuntimeError):
                    report_mod.render_all(store, Path(tmp) / "bad", include_fulltext=True)
            finally:
                store.close()

    def test_empty_report_and_schema_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                payload = reconcile_mod.build_report(store)
                self.assertEqual(payload["counts"]["groups"], 0)
                self.assertEqual(payload["findings"], [])
                self.assertIn("No entity/outcome groups", reconcile_mod.format_report(payload))
                self.assertEqual(store.schema_version, 8)
            finally:
                store.close()

    def test_abstract_claims_without_snapshot_are_not_compared(self):
        from ntx.ingest import doc_from_epmc

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                sentence = "Oxytocin increased trust."
                doc = doc_from_epmc(
                    {
                        "id": "2",
                        "pmid": "1002",
                        "title": "Oxytocin",
                        "abstractText": sentence,
                    }
                )
                store.add_document(doc)
                start = doc.text().find(sentence)
                store.add_claims(
                    [
                        Claim(
                            doc_id=doc.doc_id,
                            entity_id="NP:OXT",
                            direction="increase",
                            outcome="social_cognition",
                            sentence=sentence,
                            start=start,
                            end=start + len(sentence),
                        )
                    ]
                )
                payload = reconcile_mod.build_report(store)
                self.assertEqual(payload["counts"]["abstract_claims"], 0)
                self.assertEqual(payload["counts"]["documents"], 0)
                self.assertEqual(payload["counts"]["groups"], 0)
            finally:
                store.close()

    def test_reference_cap_truncation_and_threshold_validation(self):
        rows = [
            {
                "claim_id": index,
                "direction": "increase",
                "outcome": "social_cognition",
                "sentence": "x" * 400,
                "start": index * 10,
                "end": index * 10 + 8,
            }
            for index in range(6)
        ]
        refs = reconcile_mod._refs(rows, "abstract")
        self.assertEqual(len(refs), reconcile_mod.MAX_REFS)
        self.assertEqual(len(refs[0]["excerpt"]), reconcile_mod.MAX_EXCERPT)
        self.assertTrue(refs[0]["excerpt"].endswith("..."))
        payload = {"findings": [{"severity": "low"}]}
        self.assertTrue(reconcile_mod.should_fail(payload, "low"))
        self.assertFalse(reconcile_mod.should_fail(payload, "medium"))
        with self.assertRaises(ValueError):
            reconcile_mod.should_fail(payload, "unknown")

    def test_build_report_checks_abstract_provenance_and_by_entity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(tmp)
            try:
                payload = reconcile_mod.build_report(store)
                grouped = reconcile_mod.by_entity(payload)
                self.assertEqual(grouped["NP:OXT"]["documents"], 1)
                self.assertEqual(grouped["NP:OXT"]["counts"]["corroborated"], 1)
                self.assertEqual(grouped["NP:OXT"]["severity_counts"]["info"], 1)
                store.conn.execute("UPDATE claims SET sentence = 'tampered'")
                store.conn.commit()
                with self.assertRaisesRegex(RuntimeError, "provenance"):
                    reconcile_mod.build_report(store)
            finally:
                store.close()

    def test_cli_report_with_fulltext_writes_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._setup(tmp)
            db = store.path
            store.close()
            out = Path(tmp) / "cli-full"
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli_mod.main(["report", "--db", str(db), "--out", str(out), "--with-fulltext"])
            self.assertEqual(code, 0)
            self.assertIn(
                "Full-text reconciliation",
                (out / "oxt.md").read_text(encoding="utf-8"),
            )


class TestIngestionAdapters(unittest.TestCase):
    def test_doc_from_epmc_maps_fields(self):
        result = {
            "pmid": "123",
            "doi": "10.1/X",
            "title": "T <b>x</b>",
            "abstractText": "A &amp; B",
            "pubYear": "2020",
            "journalTitle": "J",
            "license": "cc by",
            "pubTypeList": {"pubType": ["Journal Article"]},
        }
        doc = ingest_mod.doc_from_epmc(result, fetched_at="2020-01-01T00:00:00+00:00")
        self.assertEqual(doc.doc_id, "PMID:123")
        self.assertEqual(doc.title, "T x")
        self.assertEqual(doc.abstract, "A & B")
        self.assertEqual(doc.year, 2020)
        self.assertEqual(doc.journal, "J")
        self.assertEqual(doc.pubtypes, ["Journal Article"])
        self.assertEqual(doc.fetched_at, "2020-01-01T00:00:00+00:00")

    def test_doc_id_fallbacks(self):
        self.assertEqual(ingest_mod.doc_from_epmc({"doi": "10.2/Y", "id": "e1"}).doc_id, "DOI:10.2/y")
        self.assertEqual(ingest_mod.doc_from_epmc({"id": "e2"}).doc_id, "EPMC:e2")

    def test_apply_pubmed_merges_types_and_doi(self):
        doc = ingest_mod.doc_from_epmc({"pmid": "1", "title": "T"})
        ingest_mod.apply_pubmed(
            doc,
            {
                "pubtype": ["Randomized Controlled Trial"],
                "articleids": [{"idtype": "doi", "value": "10.3/z"}],
            },
        )
        self.assertIn("Randomized Controlled Trial", doc.pubtypes)
        self.assertEqual(doc.doi, "10.3/z")
        retracted = ingest_mod.doc_from_epmc({"pmid": "2", "title": "T"})
        ingest_mod.apply_pubmed(retracted, {"pubtype": ["Retracted Publication"]})
        self.assertEqual(retracted.retraction_status, "retracted")

    def test_crossref_and_annotation_adapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("ntx.ingest.http.get_json", return_value={"message": {"id": "x"}}):
                self.assertEqual(ingest_mod.crossref_work("10.1/a", Path(tmp)), {"message": {"id": "x"}})
            self.assertIsNone(ingest_mod.crossref_work("", Path(tmp)))
            with mock.patch("ntx.ingest.http.get_json", side_effect=RuntimeError("down")):
                self.assertEqual(ingest_mod.annotations_for(["1"], Path(tmp)), {})
            payload = [{"extId": "1", "annotations": [{"exact": "oxytocin"}]}]
            with mock.patch("ntx.ingest.http.get_json", return_value=payload):
                out = ingest_mod.annotations_for(["1", "1"], Path(tmp))
            self.assertEqual(out["1"][0]["exact"], "oxytocin")

    def test_ingest_query_filters_and_enriches(self):
        results = [
            {"pmid": "11", "title": "T1", "abstractText": "oxytocin increased trust"},
            {"pmid": "12", "title": ""},
        ]
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("ntx.ingest.search", return_value={"resultList": {"result": results}}),
            mock.patch("ntx.ingest.pubmed_summaries", return_value={"11": {"pubtype": ["Meta-Analysis"]}}),
            mock.patch("ntx.ingest.annotations_for", return_value={"11": [{"exact": "oxytocin"}]}),
        ):
            docs, annotations = ingest_mod.ingest_query("q", 5, Path(tmp))
        self.assertEqual([d.doc_id for d in docs], ["PMID:11"])
        self.assertIn("Meta-Analysis", docs[0].pubtypes)
        self.assertIn("11", annotations)


class TestAnnotationsAndFlags(unittest.TestCase):
    def test_annotation_import_uses_uniprot_and_skips_noise(self):
        entity = next(e for e in config.ontology()["entities"] if e.get("uniprot"))
        text = "Results " + entity["name"] + " was elevated."
        annotation = {
            "exact": entity["name"],
            "prefix": "Results ",
            "section": "Results",
            "tags": [{"uri": "http://purl.uniprot.org/uniprot/" + entity["uniprot"]}],
        }
        mentions = LEX.mentions_from_annotations(text, [annotation])
        self.assertEqual(len(mentions), 1)
        self.assertEqual(text[mentions[0].start : mentions[0].end], entity["name"])
        self.assertEqual(LEX.mentions_from_annotations(text, [dict(annotation, section="References")]), [])
        self.assertEqual(LEX.mentions_from_annotations(text, [{"exact": entity["name"], "tags": []}]), [])

    def test_document_flags_surface_terminology_traps(self):
        flags = document_flags("A neurotropic virus; neurotrophic factor; nootropic claim.")
        self.assertTrue(any("neurotropic" in flag for flag in flags))
        self.assertIn("mentions nootropics", flags)


class TestReportsAndCli(unittest.TestCase):
    def test_render_all_refuses_broken_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "r.db")
            try:
                store.conn.execute(
                    "INSERT INTO documents (doc_id,title,abstract) VALUES ('D1','T','Body text here.')"
                )
                store.conn.execute(
                    "INSERT INTO claims (doc_id,entity_id,direction,outcome,sentence,start,end)"
                    " VALUES ('D1','NP:OXT','increase','other','not in text',0,5)"
                )
                store.conn.commit()
                with self.assertRaises(RuntimeError):
                    report_mod.render_all(store, Path(tmp) / "out")
            finally:
                store.close()

    def test_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = {"NTX_ROOT": tmp, "NTX_BUILD": str(tmp_path / "build")}
            db = str(tmp_path / "demo.db")
            out = str(tmp_path / "reports")
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()) as buffer:
                self.assertEqual(cli_mod.main(["--frozen-time", "demo", "--db", db, "--out", out]), 0)
                self.assertIn("provenance:  0", buffer.getvalue())
                self.assertTrue((tmp_path / "reports" / "oxt.html").exists())
                self.assertEqual(cli_mod.main(["check", "--db", db]), 0)
                self.assertEqual(cli_mod.main(["report", "--db", db, "--out", out]), 0)
                self.assertEqual(cli_mod.main(["discover", "--db", db, "--top", "3"]), 0)
                with contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(
                        cli_mod.main(["eval", "--db", db, "--gold", str(ROOT / "gold" / "pilot.jsonl")]),
                        2,
                    )
                self.assertIn("gold is redacted", errors.getvalue())
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(
                        cli_mod.main(["demo", "--corpus", str(tmp_path / "missing.json"), "--db", db]), 2
                    )

    def test_cli_from_fixture_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            corpus = {
                "meta": {},
                "results": [
                    {
                        "pmid": "999",
                        "title": "Oxytocin trial",
                        "abstractText": "Oxytocin increased trust in a randomized trial.",
                    }
                ],
                "annotations": {},
            }
            corpus_path = tmp_path / "corpus.json"
            corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
            with mock.patch.dict(os.environ, {"NTX_ROOT": tmp}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    cli_mod.main(
                        ["from-fixture", "--corpus", str(corpus_path), "--db", str(tmp_path / "fx.db")]
                    ),
                    0,
                )
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                cli_mod.main(["--version"])


class TestLoggingAndPaths(unittest.TestCase):
    def test_json_formatter_and_idempotent_configure(self):
        record = logging.LogRecord("ntx.test", logging.INFO, __file__, 1, "hello", None, None)
        payload = json.loads(log_mod.JsonFormatter().format(record))
        self.assertEqual(payload["message"], "hello")
        self.assertEqual(payload["level"], "INFO")
        log_mod.configure("INFO")
        log_mod.configure("INFO")
        json_handlers = [
            handler
            for handler in logging.getLogger().handlers
            if type(handler.formatter).__name__ == "JsonFormatter"
        ]
        self.assertEqual(len(json_handlers), 1)

    def test_environment_overrides_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "d"
            build = Path(tmp) / "b"
            reports = Path(tmp) / "r"
            db = Path(tmp) / "x.db"
            env = {
                "NTX_ROOT": tmp,
                "NTX_DATA": str(data),
                "NTX_BUILD": str(build),
                "NTX_REPORTS": str(reports),
                "NTX_DB": str(db),
                "NTX_CORPUS": str(Path(tmp) / "c.json"),
            }
            with mock.patch.dict(os.environ, env):
                self.assertTrue(config.data_dir().is_dir())
                self.assertTrue(config.build_dir().is_dir())
                self.assertTrue(config.reports_dir().is_dir())
                self.assertEqual(build_paths.default_db(), db)
                self.assertEqual(build_paths.demo_corpus(), Path(tmp) / "c.json")


def _gold_record(doc_id="PMID:1", direction="decrease", outcome="anxiety", status=None):
    record = {
        "schema_version": 2,
        "doc_id": doc_id,
        "mentions": [
            {"entity_id": "NP:OXT", "start": 0, "end": 8, "surface": "Oxytocin", "mention_form": "peptide"}
        ],
        "claims": [
            {
                "entity_id": "NP:OXT",
                "start": 0,
                "end": 48,
                "sentence": "Oxytocin reduced anxiety in the open field test.",
                "direction": direction,
                "outcome": outcome,
                "role": "intervention",
                "design": "animal",
            }
        ],
    }
    if status:
        record["status"] = status
    return record


class TestGoldSchema(unittest.TestCase):
    TEXT = "Oxytocin reduced anxiety in the open field test."

    def test_valid_record_passes_with_and_without_text(self):
        record = _gold_record()
        self.assertEqual(annotation.validate_record(record), [])
        self.assertEqual(annotation.validate_record(record, self.TEXT), [])

    def test_span_and_sentence_mismatch_is_reported(self):
        record = _gold_record()
        record["mentions"][0]["end"] = 9
        record["claims"][0]["sentence"] = "different sentence"
        problems = annotation.validate_record(record, self.TEXT)
        self.assertTrue(any("surface does not match" in p for p in problems))
        self.assertTrue(any("sentence does not match" in p for p in problems))

    def test_unknown_enums_and_duplicates_are_reported(self):
        record = _gold_record(direction="sideways", outcome="vibes")
        record["claims"].append(dict(record["claims"][0]))
        record["mentions"].append(dict(record["mentions"][0]))
        problems = annotation.validate_record(record, self.TEXT)
        self.assertTrue(any("unknown direction" in p for p in problems))
        self.assertTrue(any("unknown outcome" in p for p in problems))
        self.assertTrue(any("duplicate claim" in p for p in problems))
        self.assertTrue(any("duplicate mention span" in p for p in problems))

    def test_out_of_bounds_and_wrong_schema(self):
        record = _gold_record()
        record["schema_version"] = 1
        record["claims"][0]["end"] = 500
        problems = annotation.validate_record(record, self.TEXT)
        self.assertTrue(any("schema_version" in p for p in problems))
        self.assertTrue(any("exceeds text length" in p for p in problems))

    def test_optional_effect_fields_validate(self):
        record = _gold_record()
        claim = record["claims"][0]
        claim.update(
            {
                "effect_type": "smd",
                "effect_size": -0.42,
                "effect_ci_low": -0.71,
                "effect_ci_high": -0.13,
            }
        )
        self.assertEqual(annotation.validate_record(record, self.TEXT), [])
        claim["effect_type"] = "not_a_type"
        claim["effect_ci_low"] = 1.0
        problems = annotation.validate_record(record, self.TEXT)
        self.assertTrue(any("unknown effect_type" in p for p in problems))
        self.assertTrue(any("CI is inverted" in p for p in problems))

    def test_agreement_pairs_only_uses_shared_keys(self):
        left = [_gold_record("PMID:1", direction="decrease"), _gold_record("PMID:2", direction="increase")]
        right = [_gold_record("PMID:1", direction="decrease"), _gold_record("PMID:3", direction="none")]
        self.assertEqual(annotation.agreement_pairs(left, right), [("decrease", "decrease")])

    def test_effect_agreement_type_value_and_ci(self):
        left = _gold_record()
        right = _gold_record()
        left["claims"][0].update(
            {"effect_type": "smd", "effect_size": 0.40, "effect_ci_low": 0.10, "effect_ci_high": 0.70}
        )
        right["claims"][0].update(
            {"effect_type": "smd", "effect_size": 0.45, "effect_ci_low": 0.15, "effect_ci_high": 0.75}
        )
        result = annotation.effect_agreement([left], [right])
        self.assertEqual(result["compared_items"], 1)
        self.assertEqual(result["effect_type"]["kappa"], 1.0)
        self.assertAlmostEqual(result["effect_value_mae"], 0.05)
        self.assertEqual(result["effect_ci_overlap_rate"], 1.0)
        right["claims"][0]["effect_type"] = "odds_ratio"
        mismatch = annotation.effect_agreement([left], [right])
        self.assertEqual(mismatch["effect_type"]["kappa"], 0.0)
        self.assertIsNone(mismatch["effect_value_mae"])
        self.assertEqual(len(mismatch["effect_type_mismatch_examples"]), 1)
        right["claims"][0].update({"effect_type": "smd", "effect_ci_low": 0.80, "effect_ci_high": 0.95})
        separated = annotation.effect_agreement([left], [right])
        self.assertEqual(separated["effect_ci_overlap_rate"], 0.0)
        left["claims"][0].pop("effect_ci_low")
        left["claims"][0].pop("effect_ci_high")
        missing_ci = annotation.effect_agreement([left], [right])
        self.assertEqual(missing_ci["effect_ci_n"], 0)
        self.assertIsNone(missing_ci["effect_ci_overlap_rate"])
        left["claims"][0].pop("effect_type")
        left["claims"][0].pop("effect_size")
        unpaired = annotation.effect_agreement([left], [right])
        self.assertEqual(unpaired["compared_items"], 0)

    def test_merge_deduplicates_and_adjudication_overrides(self):
        merged = annotation.merge_records([_gold_record()], [_gold_record(direction="none")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["claims"]), 2)
        adjudicated = annotation.apply_adjudications(
            merged,
            [
                {
                    "doc_id": "PMID:1",
                    "entity_id": "NP:OXT",
                    "start": 0,
                    "field": "direction",
                    "value": "decrease",
                }
            ],
        )
        self.assertEqual([claim["direction"] for claim in adjudicated[0]["claims"]], ["decrease", "decrease"])


class TestAgreementStats(unittest.TestCase):
    def test_kappa_perfect_and_chance(self):
        self.assertEqual(stats.cohen_kappa([("a", "a"), ("b", "b")])["kappa"], 1.0)
        chance = stats.cohen_kappa([("a", "a"), ("a", "b"), ("b", "b"), ("b", "b")])
        self.assertAlmostEqual(chance["observed_agreement"], 0.75)
        self.assertAlmostEqual(chance["kappa"], 0.5)
        self.assertEqual(stats.cohen_kappa([])["kappa"], 0.0)

    def test_bootstrap_is_deterministic_and_brackets_point_estimate(self):
        rows = [(1, 0, 0), (0, 1, 0), (1, 1, 0), (2, 0, 1)]
        first = stats.bootstrap_prf(rows, n_boot=200, seed=7)
        second = stats.bootstrap_prf(rows, n_boot=200, seed=7)
        self.assertEqual(first, second)
        self.assertLessEqual(first["f1"]["low"], first["f1"]["value"])
        self.assertGreaterEqual(first["f1"]["high"], first["f1"]["value"])
        self.assertEqual(stats.bootstrap_prf([])["f1"]["value"], 0.0)


class TestRedactedGold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.title = "Synthetic study"
        self.abstract = "Oxytocin improved memory."
        self.text = (self.title + "\n\n" + self.abstract).strip()
        self.start = self.text.index("Oxytocin")
        self.end = self.start + len("Oxytocin improved memory.")
        self.record = {
            "schema_version": 2,
            "doc_id": "PMID:1",
            "annotator": "AB",
            "status": "human",
            "mentions": [
                {
                    "entity_id": "NP:OXT",
                    "start": self.start,
                    "end": self.start + 8,
                    "surface": "Oxytocin",
                }
            ],
            "claims": [
                {
                    "entity_id": "NP:OXT",
                    "start": self.start,
                    "end": self.end,
                    "sentence": "Oxytocin improved memory.",
                    "direction": "increase",
                    "outcome": "memory",
                    "role": "intervention",
                    "design": "rct",
                }
            ],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_redact_hydrate_roundtrip_and_tamper_detection(self):
        redacted = annotation.redact_record(self.record)
        self.assertTrue(redacted["redacted"])
        self.assertNotIn("sentence", redacted["claims"][0])
        self.assertNotIn("surface", redacted["mentions"][0])
        self.assertEqual(annotation.validate_record(redacted), [])
        self.assertTrue(annotation.is_redacted(redacted))
        missing = annotation.validate_record(redacted, self.text)
        self.assertTrue(any("hydrate redacted gold" in error for error in missing))
        hydrated = annotation.hydrate_record(redacted, self.text)
        self.assertFalse(annotation.is_redacted(hydrated))
        self.assertEqual(hydrated["claims"][0]["sentence"], "Oxytocin improved memory.")
        self.assertEqual(hydrated["mentions"][0]["surface"], "Oxytocin")
        self.assertEqual(annotation.validate_record(hydrated, self.text), [])
        broken = json.loads(json.dumps(redacted))
        broken["claims"][0]["sentence_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            annotation.hydrate_record(broken, self.text)
        outside = json.loads(json.dumps(redacted))
        outside["claims"][0]["start"] = len(self.text) + 1
        with self.assertRaises(ValueError):
            annotation.hydrate_record(outside, self.text)

    def test_validation_error_paths(self):
        bad = json.loads(json.dumps(self.record))
        bad["doc_id"] = ""
        bad["schema_version"] = 1
        bad["mentions"] = [{"entity_id": "", "start": "0", "end": 1}]
        bad["claims"] = [{"entity_id": "", "start": 0, "end": "1"}]
        errors = annotation.validate_record(bad)
        self.assertTrue(any("missing doc_id" in error for error in errors))
        self.assertTrue(any("missing entity_id" in error for error in errors))
        self.assertTrue(any("must be integers" in error for error in errors))

        bad = json.loads(json.dumps(self.record))
        bad["mentions"] = [
            {
                "entity_id": "NP:OXT",
                "start": self.start,
                "end": self.start + 8,
                "surface_sha256": "x" * 64,
                "mention_form": "bogus",
            },
            {"entity_id": "NP:OXT", "start": self.start, "end": self.start + 8},
        ]
        bad["claims"] = [
            {
                "entity_id": "NP:OXT",
                "start": self.start,
                "end": self.end,
                "sentence_sha256": "x" * 64,
                "direction": "bogus",
                "outcome": "bogus",
                "role": "bogus",
                "design": "bogus",
                "effect_size": "nan",
                "effect_ci_low": "x",
                "effect_ci_high": "y",
                "quant": "not-an-object",
            },
            {"entity_id": "NP:OXT", "start": self.start, "end": self.end},
        ]
        errors = annotation.validate_record(bad)
        for needle in (
            "surface_sha256 must be 64",
            "missing surface or surface_sha256",
            "unknown mention_form",
            "sentence_sha256 must be 64",
            "missing sentence or sentence_sha256",
            "unknown direction",
            "unknown outcome",
            "unknown role",
            "unknown design",
            "effect_size requires effect_type",
            "effect_size must be a finite number",
            "effect CI bounds must be finite numbers",
            "quant must be an object",
        ):
            self.assertTrue(any(needle in error for error in errors), needle)

        mismatch = annotation.redact_record(self.record)
        mismatch["mentions"][0]["surface"] = "Oxytocin"
        mismatch["mentions"][0]["surface_sha256"] = "0" * 64
        mismatch["claims"][0]["sentence"] = "Oxytocin improved memory."
        mismatch["claims"][0]["sentence_sha256"] = "0" * 64
        errors = annotation.validate_record(mismatch)
        self.assertTrue(any("surface_sha256 does not match" in error for error in errors))
        self.assertTrue(any("sentence_sha256 does not match" in error for error in errors))

        ci_only = json.loads(json.dumps(self.record))
        ci_only["claims"][0].update(effect_ci_low=0.1, effect_ci_high=0.2)
        self.assertTrue(
            any("effect CI requires effect_size" in error for error in annotation.validate_record(ci_only))
        )

        inverted = json.loads(json.dumps(self.record))
        inverted["claims"][0].update(
            effect_type="smd", effect_size=0.2, effect_ci_low=1.0, effect_ci_high=0.5
        )
        self.assertTrue(
            any("effect CI is inverted" in error for error in annotation.validate_record(inverted))
        )

        bad_quant = json.loads(json.dumps(self.record))
        bad_quant["claims"][0]["quant"] = {"schema": "not-ntx"}
        self.assertTrue(any("quant:" in error for error in annotation.validate_record(bad_quant)))
        no_sentence_quant = json.loads(json.dumps(bad_quant))
        no_sentence_quant["claims"][0].pop("sentence")
        no_sentence_quant["claims"][0]["sentence_sha256"] = hashlib.sha256(b"x").hexdigest()
        annotation.validate_record(no_sentence_quant)

    def test_claim_effect_fallbacks(self):
        self.assertIsNone(annotation._claim_effect({"entity_id": "NP:OXT"}))
        from_quant = annotation._claim_effect(
            {
                "entity_id": "NP:OXT",
                "quant": {"effects": [{"type": "smd", "value": 0.4}]},
            }
        )
        self.assertEqual(from_quant["type"], "smd")
        explicit = annotation._claim_effect({"entity_id": "NP:OXT", "effect_type": "smd", "effect_size": 0.3})
        self.assertEqual(explicit["value"], 0.3)

    def test_redaction_hydration_and_span_edge_paths(self):
        without_text = json.loads(json.dumps(self.record))
        without_text["mentions"][0].pop("surface")
        without_text["claims"][0].pop("sentence")
        redacted = annotation.redact_record(without_text)
        self.assertNotIn("surface_sha256", redacted["mentions"][0])
        self.assertNotIn("sentence_sha256", redacted["claims"][0])
        restored = annotation.hydrate_record(without_text, self.text)
        self.assertEqual(restored["mentions"][0]["surface"], "Oxytocin")
        self.assertEqual(restored["claims"][0]["sentence"], "Oxytocin improved memory.")

        redacted = annotation.redact_record(self.record)
        redacted["claims"][0]["sentence_sha256"] = "bad"
        with self.assertRaises(ValueError):
            annotation.hydrate_record(redacted, self.text)

        short_text = self.text[: self.start + 4]
        self.assertTrue(
            any(
                "span exceeds text length" in error
                for error in annotation.validate_record(self.record, short_text)
            )
        )
        reversed_span = json.loads(json.dumps(self.record))
        reversed_span["claims"][0]["start"] = self.end
        reversed_span["claims"][0]["end"] = self.start
        self.assertTrue(
            any("requires 0 <= start < end" in error for error in annotation.validate_record(reversed_span))
        )

    def test_hydration_rejects_bad_mentions_and_absent_text(self):
        redacted = annotation.redact_record(self.record)
        outside = json.loads(json.dumps(redacted))
        outside["mentions"][0]["start"] = len(self.text) + 1
        with self.assertRaises(ValueError):
            annotation.hydrate_record(outside, self.text)
        invalid = json.loads(json.dumps(redacted))
        invalid["mentions"][0]["surface_sha256"] = "not-a-hash"
        with self.assertRaises(ValueError):
            annotation.hydrate_record(invalid, self.text)
        mismatch = json.loads(json.dumps(redacted))
        mismatch["mentions"][0]["surface_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            annotation.hydrate_record(mismatch, self.text)
        claim_only = dict(redacted)
        claim_only.pop("redacted")
        self.assertTrue(annotation.is_redacted(claim_only))

    def test_cli_redact_hydrate_and_guards(self):
        full = self.base / "full.jsonl"
        redacted = self.base / "redacted.jsonl"
        hydrated = self.base / "hydrated.jsonl"
        corpus = self.base / "corpus.json"
        annotation.write_records([self.record], full)
        corpus.write_text(
            json.dumps({"documents": [{"doc_id": "PMID:1", "title": self.title, "abstract": self.abstract}]}),
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_mod.main(["gold", "redact", "--in", str(full), "--out", str(redacted)]), 0)
            self.assertEqual(cli_mod.main(["gold", "redact", "--in", str(full), "--out", str(redacted)]), 3)
            self.assertEqual(
                cli_mod.main(["gold", "redact", "--in", str(full), "--out", str(redacted), "--force"]),
                0,
            )
            self.assertEqual(
                cli_mod.main(
                    [
                        "gold",
                        "hydrate",
                        "--in",
                        str(redacted),
                        "--corpus",
                        str(corpus),
                        "--out",
                        str(hydrated),
                    ]
                ),
                0,
            )
            self.assertEqual(
                cli_mod.main(
                    [
                        "gold",
                        "hydrate",
                        "--in",
                        str(redacted),
                        "--corpus",
                        str(corpus),
                        "--out",
                        str(hydrated),
                    ]
                ),
                3,
            )
        loaded = annotation.load_records(hydrated)
        self.assertEqual(loaded[0]["claims"][0]["sentence"], "Oxytocin improved memory.")
        self.assertFalse(annotation.is_redacted(loaded[0]))

        tampered = json.loads(json.dumps(annotation.load_records(redacted)[0]))
        tampered["claims"][0]["sentence_sha256"] = "0" * 64
        tampered_path = self.base / "tampered.jsonl"
        annotation.write_records([tampered], tampered_path)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                cli_mod.main(
                    [
                        "gold",
                        "hydrate",
                        "--in",
                        str(tampered_path),
                        "--corpus",
                        str(corpus),
                        "--out",
                        str(self.base / "tampered-full.jsonl"),
                    ]
                ),
                2,
            )
        missing_corpus = self.base / "missing.json"
        missing_corpus.write_text(json.dumps({"documents": []}), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                cli_mod.main(
                    [
                        "gold",
                        "hydrate",
                        "--in",
                        str(redacted),
                        "--corpus",
                        str(missing_corpus),
                        "--out",
                        str(self.base / "nope.jsonl"),
                    ]
                ),
                3,
            )


class TestReleaseMetadata(unittest.TestCase):
    def test_checker_accepts_release_and_rejects_wrong_tag(self):
        ok = subprocess.run(
            [sys.executable, "scripts/check_release.py", "--tag", "v0.18.0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("release metadata: ok", ok.stdout)
        bad = subprocess.run(
            [sys.executable, "scripts/check_release.py", "--tag", "v0.16.0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertIn("does not match version", bad.stderr)


class TestGoldCli(unittest.TestCase):
    def test_validate_agreement_and_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            corpus = tmp_path / "corpus.json"
            corpus.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "doc_id": "PMID:1",
                                "title": "",
                                "abstract": "Oxytocin reduced anxiety in the open field test.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            left = tmp_path / "left.jsonl"
            right = tmp_path / "right.jsonl"
            annotation.write_records([_gold_record()], left)
            annotation.write_records([_gold_record(direction="none")], right)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli_mod.main(["gold", "validate", "--file", str(left), "--corpus", str(corpus)])
            self.assertEqual(code, 0, out.getvalue())

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli_mod.main(["gold", "agreement", "--a", str(left), "--b", str(right)])
            payload = json.loads(out.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["field"], "direction")
            self.assertEqual(payload["compared_items"], 1)

            merged_path = tmp_path / "merged.jsonl"
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli_mod.main(
                    [
                        "gold",
                        "merge",
                        "--primary",
                        str(left),
                        "--secondary",
                        str(right),
                        "--out",
                        str(merged_path),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(len(annotation.load_records(merged_path)[0]["claims"]), 2)

    def test_validate_returns_nonzero_on_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            record = _gold_record()
            record["claims"][0]["direction"] = "sideways"
            annotation.write_records([record], path)
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli_mod.main(["gold", "validate", "--file", str(path)])
            self.assertEqual(code, 1)


class TestGoldEdgeCases(unittest.TestCase):
    def test_missing_fields_and_bad_types_are_reported(self):
        problems = annotation.validate_record(
            {"schema_version": 2, "doc_id": "", "mentions": "x", "claims": "y"}
        )
        self.assertTrue(any("missing doc_id" in p for p in problems))
        self.assertTrue(any("mentions must be a list" in p for p in problems))
        self.assertTrue(any("claims must be a list" in p for p in problems))
        record = _gold_record()
        record["mentions"][0]["start"] = -1
        del record["claims"][0]["entity_id"]
        problems = annotation.validate_record(record)
        self.assertTrue(any("0 <= start < end" in p for p in problems))
        self.assertTrue(any("missing entity_id" in p for p in problems))

    def test_labels_join_multiple_claims_on_one_sentence(self):
        record = _gold_record()
        second = dict(record["claims"][0], direction="none", outcome="memory")
        record["claims"].append(second)
        labels = annotation.labels_by_key([record], "direction")
        self.assertEqual(labels[("PMID:1", "NP:OXT", 0)], {"decrease", "none"})
        pairs = annotation.agreement_pairs([record], [record], "direction")
        self.assertEqual(pairs, [("decrease+none", "decrease+none")])

    def test_load_write_roundtrip_strips_internal_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.jsonl"
            path.write_text(
                json.dumps(_gold_record()) + "\n\n" + json.dumps(_gold_record("PMID:2")) + "\n",
                encoding="utf-8",
            )
            records = annotation.load_records(path)
            self.assertEqual([r["doc_id"] for r in records], ["PMID:1", "PMID:2"])
            self.assertEqual(records[0]["_line"], 1)
            annotation.write_records(records, path)
            self.assertNotIn("_line", json.loads(path.read_text(encoding="utf-8").splitlines()[0]))

    def test_merge_keeps_documents_only_in_secondary(self):
        merged = annotation.merge_records([_gold_record("PMID:1")], [_gold_record("PMID:2")])
        self.assertEqual([record["doc_id"] for record in merged], ["PMID:1", "PMID:2"])

    def test_percentile_edges(self):
        self.assertEqual(stats.percentile([], 0.5), 0.0)
        self.assertEqual(stats.percentile([1.0, 2.0, 3.0], 0.0), 1.0)
        self.assertEqual(stats.percentile([1.0, 2.0, 3.0], 1.0), 3.0)
        self.assertAlmostEqual(stats.percentile([1.0, 2.0, 3.0], 0.5), 2.0)

    def test_agreement_with_disjoint_documents_is_empty(self):
        left = [_gold_record("PMID:1")]
        right = [_gold_record("PMID:9")]
        self.assertEqual(annotation.agreement_pairs(left, right), [])


class TestEvalWithConfidenceIntervals(unittest.TestCase):
    def test_bootstrap_ci_and_stratum_breakdown_are_self_consistent(self):
        from ntx.eval import evaluate_with_ci
        from ntx.ingest import doc_from_epmc

        corpus = {
            "results": [
                {
                    "id": "1",
                    "pmid": "2001",
                    "title": "Oxytocin and anxiety",
                    "abstractText": "Oxytocin reduced anxiety in rats.",
                    "journalTitle": "Test Journal",
                    "pubYear": "2024",
                    "pubTypeList": {"pubType": ["Journal Article"]},
                }
            ],
            "annotations": {},
        }
        docs = [doc_from_epmc(r) for r in corpus["results"]]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.db")
            try:
                process_documents(store, docs, corpus["annotations"])
                claims = [dict(row) for row in store.conn.execute("SELECT * FROM claims")]
                self.assertTrue(claims)
                gold = [
                    {
                        "doc_id": "PMID:2001",
                        "mentions": [],
                        "claims": [
                            {
                                "entity_id": claim["entity_id"],
                                "direction": claim["direction"],
                                "outcome": claim["outcome"],
                                "start": claim["start"],
                                "end": claim["end"],
                                "sentence": claim["sentence"],
                            }
                            for claim in claims
                        ],
                    }
                ]
                result = evaluate_with_ci(store, gold, {"PMID:2001": "preclinical"}, n_boot=100, seed=3)
            finally:
                store.close()
        self.assertEqual(result["claims_ci"]["f1"]["value"], 1.0)
        self.assertEqual(result["claims_ci"]["f1"]["low"], 1.0)
        self.assertEqual(result["by_stratum"]["preclinical"]["documents"], 1)
        self.assertEqual(result["by_stratum"]["preclinical"]["f1"], 1.0)


class TestExtractionV2(unittest.TestCase):
    def test_clause_split_keeps_exact_spans(self):
        text = "Oxytocin improved memory; however, it did not change anxiety in adults."
        spans = split_clauses(text)
        self.assertGreaterEqual(len(spans), 2)
        for start, end in spans:
            self.assertTrue(text[start:end].strip())
            self.assertNotIn(";", text[start:end])

    def test_contrast_direction_uses_the_entity_clause(self):
        text = "Oxytocin improved social cognition in adults but reduced anxiety."
        claims = extract_claims(text, LEX.detect(text))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].direction, "increase")
        self.assertIn("clause_contrast", claims[0].reasons)

    def test_negated_contrast_stays_mixed(self):
        text = "NPY had no effect after 24 h but reduced sucrose intake in rats."
        claims = extract_claims(text, LEX.detect(text))
        self.assertEqual(claims[0].direction, "mixed")

    def test_comparator_role_is_explicit(self):
        text = "Oxytocin alone increased anxiety compared with placebo in adults."
        claims = extract_claims(text, LEX.detect(text))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].role, "comparator")
        self.assertIn("role_comparator", claims[0].reasons)

    def test_evidence_span_is_inside_the_sentence(self):
        text = "In a randomized trial, oxytocin improved social cognition in 120 adults."
        claims = extract_claims(text, LEX.detect(text))
        claim = claims[0]
        self.assertEqual(text[claim.start : claim.end], claim.sentence)
        self.assertGreaterEqual(claim.evidence_start, claim.start)
        self.assertLessEqual(claim.evidence_end, claim.end)
        self.assertTrue(text[claim.evidence_start : claim.evidence_end].strip())

    def test_section_is_recorded_for_structured_abstracts(self):
        text = "Background: Oxytocin is a peptide. Results: Oxytocin improved memory in 40 adults."
        claims = extract_claims(text, LEX.detect(text))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].section, "results")
        self.assertIn("results_section", claims[0].reasons)

    def test_sections_finds_labels(self):
        labels = sections("Background: a. Results: b. Conclusions: c.")
        self.assertEqual([name for _pos, name in labels], ["background", "results", "conclusions"])

    def test_coreference_is_opt_in_and_explicit(self):
        text = (
            "Intranasal oxytocin improved social cognition in 120 adults. "
            "The peptide reduced anxiety in the same participants."
        )
        mentions = LEX.detect(text)
        baseline = extract_claims(text, mentions)
        self.assertEqual([claim.resolver for claim in baseline], ["direct"])
        resolved = extract_claims(text, mentions, flags=FeatureFlags(resolve_coref=True))
        self.assertEqual([claim.resolver for claim in resolved], ["direct", "antecedent"])
        self.assertEqual(resolved[1].entity_id, "NP:OXT")
        self.assertIn("coref_antecedent", resolved[1].reasons)

    def test_provenance_flags_evidence_outside_sentence(self):
        doc = Document(doc_id="PMID:EV", title="Oxytocin", abstract="Oxytocin improved memory in adults.")
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "ev.db")
            try:
                process_documents(store, [doc])
                row = store.conn.execute("SELECT claim_id, start FROM claims").fetchone()
                self.assertIsNotNone(row)
                store.conn.execute(
                    "UPDATE claims SET evidence_start=?, evidence_end=? WHERE claim_id=?",
                    (row["start"] - 1, row["start"] + 2, row["claim_id"]),
                )
                store.conn.commit()
                reasons = [error["reason"] for error in store.provenance_errors()]
                self.assertIn("evidence span outside sentence", reasons)
            finally:
                store.close()

    def test_report_shows_role_and_evidence_clause(self):
        row = {
            "entity_id": "NP:OXT",
            "direction": "increase",
            "outcome": "memory",
            "species": "human",
            "design": "rct",
            "band": "high",
            "rubric_points": 1.0,
            "confidence": 0.8,
            "role": "comparator",
            "sentence": "Oxytocin alone improved memory in 40 adults; placebo did not.",
            "start": 0,
            "evidence_start": 0,
            "evidence_end": 47,
            "doc_id": "PMID:1",
            "title": "Trial",
            "journal": "J",
            "year": 2024,
            "pmid": "1",
            "doi": "",
            "retraction_status": "none",
            "sample_size": 40,
        }
        text = report_mod.render_markdown("Oxytocin", "NP:OXT", [row])
        self.assertIn("role=comparator", text)
        self.assertIn("Oxytocin alone improved memory", text)

    def test_schema_v3_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mig.db"
            store = Store(path)
            try:
                doc = Document(doc_id="PMID:M", title="Oxytocin", abstract="Oxytocin improved memory.")
                process_documents(store, [doc])
                store.conn.execute("PRAGMA user_version=2")
                store.conn.commit()
                counts = store.counts()
            finally:
                store.close()
            upgraded = Store(path)
            try:
                self.assertEqual(upgraded.schema_version, SCHEMA_VERSION)
                self.assertEqual(upgraded.counts()["claims"], counts["claims"])
                columns = {row[1] for row in upgraded.conn.execute("PRAGMA table_info(claims)")}
                self.assertTrue({"role", "evidence_start", "section", "resolver", "reasons"} <= columns)
            finally:
                upgraded.close()


class TestSynthesisAndAblation(unittest.TestCase):
    def _store_with_claims(self, tmp: Path, specs: list[tuple[str, str, str]]) -> Store:
        store = Store(tmp / "syn.db")
        for doc_id, role, direction in specs:
            store.add_document(
                Document(doc_id=doc_id, title="T", abstract="Oxytocin changed memory in adults.")
            )
            store.add_claims(
                [
                    Claim(
                        doc_id=doc_id,
                        entity_id="NP:OXT",
                        direction=direction,
                        outcome="memory",
                        design="rct",
                        species="human",
                        role=role,
                        sentence="Oxytocin changed memory in adults.",
                        start=0,
                        end=35,
                        confidence=0.8,
                    )
                ]
            )
            store.conn.execute("UPDATE claims SET role=? WHERE doc_id=?", (role, doc_id))
            store.conn.commit()
        return store

    def test_synthesis_excludes_comparator_claims_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store_with_claims(
                Path(tmp),
                [
                    ("PMID:1", "intervention", "increase"),
                    ("PMID:2", "comparator", "decrease"),
                ],
            )
            try:
                default = synthesis_mod.synthesize(store, n_boot=100)
                all_roles = synthesis_mod.synthesize(store, n_boot=100, include_comparator=True)
            finally:
                store.close()
        self.assertEqual(default[0]["documents"], 1)
        self.assertEqual(default[0]["pooled_support"], 1.0)
        self.assertEqual(all_roles[0]["documents"], 2)
        self.assertEqual(all_roles[0]["pooled_support"], 0.5)

    def test_synthesis_bootstrap_interval_contains_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store_with_claims(
                Path(tmp),
                [
                    ("PMID:1", "intervention", "increase"),
                    ("PMID:2", "intervention", "increase"),
                    ("PMID:3", "intervention", "decrease"),
                    ("PMID:4", "intervention", "decrease"),
                ],
            )
            try:
                rows = synthesis_mod.synthesize(store, n_boot=400, seed=1)
            finally:
                store.close()
        row = rows[0]
        self.assertAlmostEqual(row["pooled_support"], 0.5)
        self.assertLessEqual(row["ci_low"], row["pooled_support"])
        self.assertGreaterEqual(row["ci_high"], row["pooled_support"])
        self.assertGreater(row["ci_high"], row["ci_low"])
        self.assertTrue(row["fragile"])

    def test_synthesis_helpers_and_empty_output(self):
        self.assertEqual(synthesis_mod._doc_direction({"increase", "decrease"}), "mixed")
        self.assertEqual(synthesis_mod._doc_direction(set()), "unclear")
        self.assertEqual(synthesis_mod._doc_direction({"none"}), "none")
        self.assertIsNone(synthesis_mod._pooled([(1.0, "none")]))
        self.assertEqual(synthesis_mod._bootstrap_interval([(1.0, "increase")], 10, 0), (None, None))
        self.assertIn("No claims available", synthesis_mod.format_synthesis([]))
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store_with_claims(
                Path(tmp),
                [
                    ("PMID:1", "intervention", "increase"),
                    ("PMID:2", "intervention", "decrease"),
                ],
            )
            try:
                rows = synthesis_mod.synthesize(store, n_boot=0)
            finally:
                store.close()
        rendered = synthesis_mod.format_synthesis(rows)
        self.assertIn("Evidence synthesis", rendered)
        self.assertIn("n/a", rendered)
        self.assertIn("not a probability", rendered)

    def test_ablation_is_paired_and_reports_missing_gold(self):
        docs = [
            Document(
                doc_id="PMID:1",
                title="Oxytocin",
                abstract="Oxytocin improved social cognition in 120 adults in a randomized trial.",
            ),
            Document(
                doc_id="PMID:2",
                title="NPY",
                abstract="NPY reduced food intake in rats.",
            ),
        ]
        gold = [
            {
                "doc_id": "PMID:1",
                "mentions": [],
                "claims": [
                    {
                        "entity_id": "NP:OXT",
                        "direction": "increase",
                        "outcome": "social_cognition",
                        "start": 11,
                    }
                ],
            },
            {
                "doc_id": "PMID:MISSING",
                "mentions": [],
                "claims": [{"entity_id": "NP:OXT", "direction": "increase", "outcome": "memory", "start": 0}],
            },
        ]
        result = ablate_mod.run_ablation(docs, gold, configs=["baseline", "no_clause"], n_boot=50, seed=2)
        self.assertEqual(result["documents"], 1)
        self.assertEqual(result["gold_documents_missing"], ["PMID:MISSING"])
        self.assertEqual(result["results"]["baseline"]["delta"]["value"], 0.0)
        self.assertIn("baseline", ablate_mod.format_ablation(result))

    def test_corpus_loader_reads_document_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.json"
            path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "doc_id": "PMID:1",
                                "title": "Oxytocin",
                                "abstract": "Oxytocin improved memory.",
                                "pmid": "1",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            docs, annotations = corpus_mod.load_corpus(path)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].abstract, "Oxytocin improved memory.")
        self.assertEqual(annotations, {})


class TestServerAndSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "serve.db"
        store = Store(self.db)
        try:
            store.add_document(
                Document(doc_id="PMID:1", title="Oxytocin", abstract="Oxytocin improved memory in adults.")
            )
            store.add_claims(
                [
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="increase",
                        outcome="memory",
                        design="rct",
                        species="human",
                        sentence="Oxytocin improved memory in adults.",
                        start=10,
                        end=45,
                        confidence=0.8,
                    )
                ]
            )
        finally:
            store.close()
        self.server = serve_mod.make_server(self.db, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(self.base + path) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_openapi_contract_endpoint(self):
        spec = openapi_mod.build_spec()
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertEqual(spec["info"]["version"], openapi_mod.__version__)
        self.assertEqual(set(spec["paths"]), set(serve_mod.ROUTES))
        self.assertIn("bearerAuth", spec["components"]["securitySchemes"])
        _, root = self._get("/")
        self.assertIn("/api/v1/openapi.json", root["endpoints"])
        status, served = self._get("/api/v1/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(served, spec)

    def test_health_and_counts(self):
        status, health = self._get("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["schema_version"], SCHEMA_VERSION)
        _, counts = self._get("/api/v1/counts")
        self.assertEqual(counts["claims"], 1)

    def test_quality_and_reconciliation_endpoints(self):
        _, payload = self._get("/api/v1/quality")
        self.assertEqual(payload["schema"], "ntx-quality-v2")
        self.assertEqual(
            {gate["name"] for gate in payload["gates"]}, {"schema", "provenance", "effects", "gold"}
        )
        _, report = self._get("/api/v1/reconciliation")
        self.assertEqual(report["schema"], "ntx-fulltext-report-v4")
        self.assertEqual(report["counts"]["groups"], 0)

    def test_token_and_host_guard(self):
        guarded = serve_mod.make_server(self.db, port=0, token="s3cret")
        thread = threading.Thread(target=guarded.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%d" % guarded.server_address[1]
        try:
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(base + "/api/v1/counts")
            self.assertEqual(context.exception.code, 401)
            self.assertEqual(context.exception.headers["X-Content-Type-Options"], "nosniff")
            request = urllib.request.Request(
                base + "/api/v1/counts", headers={"Authorization": "Bearer s3cret"}
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
                self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
                self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
            bad_host = urllib.request.Request(
                base + "/api/v1/counts",
                headers={"Host": "evil.example", "Authorization": "Bearer s3cret"},
            )
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(bad_host)
            self.assertEqual(context.exception.code, 403)
        finally:
            guarded.shutdown()
            guarded.server_close()
            thread.join(timeout=5)
        with self.assertRaises(PermissionError):
            serve_mod.make_server(self.db, host="0.0.0.0", port=0)
        with self.assertRaises(PermissionError):
            serve_mod.make_server(self.db, host="0.0.0.0", port=0, token="short")

    def test_root_entities_gaps_synthesis_and_manifest(self):
        _, root = self._get("/")
        self.assertEqual(root["name"], "ntx read-only API")
        self.assertIn("/api/v1/quality", root["endpoints"])
        _, entities = self._get("/api/v1/entities")
        self.assertEqual(entities["items"], [])
        _, gaps = self._get("/api/v1/gaps?top=2")
        self.assertIn("items", gaps)
        _, synthesis = self._get("/api/v1/synthesis?bootstrap=0")
        self.assertIn("items", synthesis)
        _, manifest = self._get("/api/v1/manifest/latest")
        self.assertIsNone(manifest["manifest"])

    def test_invalid_query_and_write_methods(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._get("/api/v1/synthesis?bootstrap=not-an-int")
        self.assertEqual(context.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._get("/api/v1/gaps?top=999")
        self.assertEqual(context.exception.code, 400)
        for method in ("PUT", "DELETE"):
            request = urllib.request.Request(self.base + "/api/v1/counts", method=method)
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(request)
            self.assertEqual(context.exception.code, 405)

    def test_helper_errors_and_internal_failure(self):
        item = serve_mod._row_to_dict({"reasons": "not-json", "quant": "not-json"})
        self.assertEqual(item["reasons"], [])
        self.assertEqual(item["quant"], {})
        self.assertEqual(serve_mod._int_param({}, "limit", 5, 1, 10), 5)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._get("/not-api")
        self.assertEqual(context.exception.code, 404)
        with mock.patch.object(serve_mod._Handler, "_dispatch", side_effect=RuntimeError("boom")):
            with self.assertRaises(urllib.error.HTTPError) as context:
                self._get("/api/v1/health")
            self.assertEqual(context.exception.code, 500)

    def test_claims_pagination_and_filters(self):
        _, payload = self._get("/api/v1/claims?entity=NP:OXT&limit=1&offset=0")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["role"], "intervention")
        self.assertIsInstance(payload["items"][0]["reasons"], list)

    def test_invalid_limit_is_bad_request(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._get("/api/v1/claims?limit=9999")
        self.assertEqual(context.exception.code, 400)

    def test_unknown_route_and_write_methods(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._get("/api/v1/nope")
        self.assertEqual(context.exception.code, 404)
        request = urllib.request.Request(self.base + "/api/v1/counts", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request)
        self.assertEqual(context.exception.code, 405)

    def test_missing_database_returns_503(self):
        missing = serve_mod.make_server(Path(self.tmp.name) / "absent.db", port=0)
        thread = threading.Thread(target=missing.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:%d" % missing.server_address[1]
            with urllib.request.urlopen(base + "/api/v1/openapi.json") as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["openapi"], "3.1.0")
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(base + "/api/v1/counts")
            self.assertEqual(context.exception.code, 503)
        finally:
            missing.shutdown()
            missing.server_close()
            thread.join(timeout=5)

    def test_settings_file_and_env_precedence(self):
        path = Path(self.tmp.name) / "ntx.toml"
        path.write_text(
            "[paths]\ndb = 'from-file.db'\n[server]\nport = 9001\n[eval]\nbootstrap = 77\n",
            encoding="utf-8",
        )
        loaded = settings_mod.load_settings(path)
        self.assertEqual(loaded.db, "from-file.db")
        self.assertEqual(loaded.port, 9001)
        self.assertEqual(loaded.bootstrap, 77)
        with mock.patch.dict(os.environ, {"NTX_PORT": "1234"}):
            overridden = settings_mod.load_settings(path)
        self.assertEqual(overridden.port, 1234)

    def test_invalid_settings_fail_loudly(self):
        path = Path(self.tmp.name) / "bad.toml"
        path.write_text("[server]\nport = 70000\n", encoding="utf-8")
        with self.assertRaises(settings_mod.SettingsError):
            settings_mod.load_settings(path)

    def test_settings_path_and_validation_errors(self):
        path = Path(self.tmp.name) / "ntx.toml"
        path.write_text("[paths]\ndb = 'x.db'\n", encoding="utf-8")
        self.assertEqual(settings_mod.config_path(path), path)
        with mock.patch.dict(os.environ, {"NTX_CONFIG": str(path)}):
            self.assertEqual(settings_mod.config_path(), path)
        with self.assertRaises(settings_mod.SettingsError):
            settings_mod.load_settings(Path(self.tmp.name) / "missing.toml")
        bad = Path(self.tmp.name) / "bad.toml"
        bad.write_text("this is not toml [", encoding="utf-8")
        with self.assertRaises(settings_mod.SettingsError):
            settings_mod.load_settings(bad)
        bad.write_text('paths = "not-a-table"\n', encoding="utf-8")
        with self.assertRaises(settings_mod.SettingsError):
            settings_mod.load_settings(bad)

    def test_invalid_environment_values_fail_loudly(self):
        for env, value in (
            ("NTX_DB", "   "),
            ("NTX_PORT", "not-an-int"),
            ("NTX_BOOTSTRAP", "999999"),
            ("NTX_RESOLVE_COREF", "maybe"),
        ):
            with (
                self.subTest(env=env),
                mock.patch.dict(os.environ, {env: value}),
                self.assertRaises(settings_mod.SettingsError),
            ):
                settings_mod.load_settings(None)

    def test_boolean_config_and_environment(self):
        path = Path(self.tmp.name) / "bool.toml"
        path.write_text("[extract]\nresolve_coref = 'yes'\n", encoding="utf-8")
        with self.assertRaises(settings_mod.SettingsError):
            settings_mod.load_settings(path)
        with mock.patch.dict(os.environ, {"NTX_RESOLVE_COREF": "on"}):
            self.assertTrue(settings_mod.load_settings(None).resolve_coref)
        with mock.patch.dict(os.environ, {"NTX_RESOLVE_COREF": "off"}):
            self.assertFalse(settings_mod.load_settings(None).resolve_coref)

    def test_check_exit_codes(self):
        self.assertEqual(cli_mod.main(["check", "--db", str(self.db)]), 0)
        store = Store(self.db)
        try:
            store.conn.execute("UPDATE claims SET sentence='wrong'")
            store.conn.commit()
        finally:
            store.close()
        self.assertEqual(cli_mod.main(["check", "--db", str(self.db)]), 1)


class TestQualityAndDashboard(unittest.TestCase):
    def _store(self, tmp: str) -> Store:
        text = "Oxytocin increased trust."
        store = Store(Path(tmp) / "quality.db")
        store.add_document(Document(doc_id="PMID:1", abstract=text))
        store.add_claims(
            [
                Claim(
                    doc_id="PMID:1",
                    entity_id="NP:OXT",
                    direction="increase",
                    outcome="trust",
                    sentence=text,
                    start=0,
                    end=len(text),
                )
            ]
        )
        return store

    def test_quality_gates_and_deterministic_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                with mock.patch.object(quality_mod.clock, "iso", side_effect=["a", "b"]):
                    first = quality_mod.build_quality(store)
                    second = quality_mod.build_quality(store)
                self.assertEqual(first["content_hash"], second["content_hash"])
                self.assertTrue(first["ok"])
                gates = {gate["name"]: gate["status"] for gate in first["gates"]}
                self.assertEqual(gates["schema"], "pass")
                self.assertEqual(gates["provenance"], "pass")
                self.assertEqual(gates["effects"], "warn")
                self.assertEqual(gates["gold"], "not_configured")
                self.assertEqual(first["counts"]["effect_claims"], 0)
                self.assertEqual(first["thresholds"]["min_effect_coverage"], 0.2)
                self.assertEqual(first["counts"]["effect_ambiguous_claims"], 0)
            finally:
                store.close()

    def test_dashboard_is_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                target = dashboard_mod.render_dashboard(store, Path(tmp) / "dash")
            finally:
                store.close()
            body = target.read_text(encoding="utf-8")
            self.assertIn("Quality gates", body)
            self.assertIn("Oxytocin increased trust.", body)
            self.assertNotIn("http://", body)
            self.assertNotIn("<script", body)

    def test_quality_with_gold_and_text_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                gold = [
                    {"schema_version": 2, "doc_id": "PMID:1", "status": "human", "mentions": [], "claims": []}
                ]
                payload = quality_mod.build_quality(store, gold)
                rendered = quality_mod.format_quality(payload)
            finally:
                store.close()
        self.assertIn("gold", {gate["name"] for gate in payload["gates"]})
        self.assertIn("Quality gates", rendered)
        self.assertIn("content hash:", rendered)

    def test_quality_warns_on_ambiguous_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                sentence = "OR = 1.2, RR = 1.5 (95% CI 1.1 to 1.9)."
                quant = quant_mod.extract_quant(sentence, 0)
                store.conn.execute(
                    "UPDATE claims SET quant=?, effect_type='odds_ratio', effect_size=1.2",
                    (json.dumps(quant),),
                )
                store.conn.commit()
                payload = quality_mod.build_quality(store, min_effect_coverage=0.0)
            finally:
                store.close()
        self.assertEqual(payload["counts"]["effect_ambiguous_claims"], 1)
        gates = {gate["name"]: gate["status"] for gate in payload["gates"]}
        self.assertEqual(gates["effects"], "warn")

    def test_quality_require_gold_and_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                payload = quality_mod.build_quality(store, require_gold=True)
                self.assertFalse(payload["ok"])
                self.assertIn("gold", payload["failed_gates"])
                strict = quality_mod.build_quality(store, min_effect_coverage=0.0)
                gates = {gate["name"]: gate["status"] for gate in strict["gates"]}
                self.assertEqual(gates["effects"], "pass")
                with self.assertRaises(ValueError):
                    quality_mod.build_quality(store, min_effect_coverage=2.0)
            finally:
                store.close()
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                gold = [
                    {"schema_version": 2, "doc_id": "PMID:1", "status": "human", "mentions": [], "claims": []}
                ]
                payload = quality_mod.build_quality(store, gold, require_gold=True)
            finally:
                store.close()
        self.assertFalse(payload["ok"])
        self.assertIn("gold", payload["failed_gates"])

    def test_cli_quality_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            db = store.path
            store.close()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["quality", "--db", str(db), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["schema"], "ntx-quality-v2")

    def test_cli_quality_thresholds_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            db = store.path
            store.close()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["quality", "--db", str(db), "--require-gold"])
            self.assertEqual(code, cli_mod.EXIT_INTEGRITY)
            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                code = cli_mod.main(["quality", "--db", str(db), "--min-effect-coverage", "2"])
            self.assertEqual(code, cli_mod.EXIT_USAGE)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["quality", "--db", str(db), "--min-effect-coverage", "0"])
            self.assertEqual(code, 0)
            self.assertIn("min effect coverage: 0.00", buffer.getvalue())
            self.assertIn("require gold: no", buffer.getvalue())


class TestCliTextPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = self.base / "text.db"
        self.claim_id = 0
        store = Store(self.db)
        try:
            text = "Oxytocin increased trust (Cohen's d = 0.40, 95% CI 0.10 to 0.70)."
            store.add_document(Document(doc_id="PMID:1", title="", abstract=text))
            claim = Claim(
                doc_id="PMID:1",
                entity_id="NP:OXT",
                direction="increase",
                outcome="trust",
                sentence=text,
                start=0,
                end=len(text),
                effect_type="smd",
                effect_size=0.40,
                effect_ci_low=0.10,
                effect_ci_high=0.70,
            )
            store.add_claims([claim])
            self.claim_id = int(store.conn.execute("SELECT claim_id FROM claims").fetchone()["claim_id"])
        finally:
            store.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli_mod.main(list(args))
        return code, out.getvalue()

    def test_text_dashboard_synthesis_and_mechanism(self):
        code, text = self._run("dashboard", "--db", str(self.db), "--out", str(self.base / "dash"))
        self.assertEqual(code, 0)
        self.assertTrue((self.base / "dash" / "index.html").exists())
        code, text = self._run("synthesize", "--db", str(self.db), "--bootstrap", "0")
        self.assertEqual(code, 0)
        self.assertIn("synthesis", text.lower())
        code, text = self._run("mechanism", "edges", "--db", str(self.db), "--min-support", "1")
        self.assertEqual(code, 0)
        code, text = self._run("mechanism", "check", "--db", str(self.db), "--holdout", "0")
        self.assertEqual(code, 0)
        code, text = self._run("discover", "--db", str(self.db), "--top", "3")
        self.assertEqual(code, 0)

    def test_text_gold_progress_diff_and_conflicts(self):
        draft = ROOT / "gold" / "v2" / "draft.jsonl"
        code, text = self._run("gold", "progress", "--gold", str(draft))
        self.assertEqual(code, 0)
        self.assertIn("documents", text)
        code, text = self._run("gold", "diff", "--a", str(draft), "--b", str(draft))
        self.assertEqual(code, 0)
        self.assertIn("claims only in A", text)
        code, text = self._run(
            "gold",
            "conflicts",
            "--a",
            str(draft),
            "--b",
            str(draft),
            "--out",
            str(self.base / "adjudications.jsonl"),
        )
        self.assertEqual(code, 0)
        self.assertIn("field conflicts", text)
        frame = self.base / "readiness_frame.json"
        frame.write_text(
            json.dumps({"documents": [{"doc_id": "PMID:NOPE", "stratum": "x"}]}), encoding="utf-8"
        )
        code, text = self._run("gold", "readiness", "--gold", str(draft), "--frame", str(frame))
        self.assertEqual(code, 3)
        self.assertIn("ready:", text)
        self.assertIn("blockers:", text)
        code, text = self._run("gold", "validate", "--file", str(draft))
        self.assertEqual(code, 0)

    def test_text_audit_and_precursor_list(self):
        worksheet = self.base / "worksheet.md"
        template = self.base / "verdicts.jsonl"
        code, text = self._run(
            "audit",
            "sample",
            "--db",
            str(self.db),
            "--n",
            "5",
            "--out",
            str(worksheet),
            "--verdicts-out",
            str(template),
        )
        self.assertEqual(code, 0)
        self.assertIn("sampled claims", text)
        rows = [json.loads(line) for line in template.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row["verdict"] = "support"
            row["effect_verdict"] = "correct"
        template.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        code, text = self._run("audit", "score", "--db", str(self.db), "--verdicts", str(template))
        self.assertEqual(code, 0)
        self.assertIn("effect audit", text)
        code, text = self._run("precursor", "list", "--db", str(self.db))
        self.assertEqual(code, 0)

    def test_mocked_serve_fulltext_and_enrich_paths(self):
        code, text = self._run("serve", "--db", str(self.base / "absent.db"))
        self.assertEqual(code, 1)
        with mock.patch.object(serve_mod, "serve", side_effect=KeyboardInterrupt):
            code, text = self._run("serve", "--db", str(self.db))
        self.assertEqual(code, 0)
        self.assertIn("ntx API", text)
        with mock.patch.object(
            serve_mod, "serve", side_effect=PermissionError("non-loopback requires token")
        ):
            code, text = self._run("serve", "--db", str(self.db), "--host", "0.0.0.0")
        self.assertEqual(code, 3)
        payload = {
            "schema": fulltext_mod.FETCH_SCHEMA,
            "counts": dict.fromkeys(fulltext_mod.FETCH_STATUSES, 0),
            "items": [{"doc_id": "PMID:1", "status": "unavailable", "detail": "no pmcid"}],
        }
        with mock.patch.object(fulltext_mod, "fetch_fulltext", return_value=payload):
            code, text = self._run("fulltext", "fetch", "--db", str(self.db))
            self.assertEqual(code, 0)
            self.assertIn("full-text fetch", text)
            code, text = self._run("fulltext", "fetch", "--db", str(self.db), "--json")
            self.assertEqual(code, 0)
        code, text = self._run("fulltext", "report", "--db", str(self.db), "--out", str(self.base))
        self.assertEqual(code, 2)
        code, text = self._run(
            "fulltext", "report", "--db", str(self.db), "--out", str(self.base / "report.md")
        )
        self.assertEqual(code, 0)
        self.assertIn("report:", text)
        code, text = self._run("discover", "--db", str(self.db), "--json")
        self.assertEqual(code, 0)
        code, text = self._run("synthesize", "--db", str(self.db), "--out", str(self.base / "synth.md"))
        self.assertEqual(code, 0)
        enriched = {
            "accession": "P30559",
            "protein_name": "Oxytocin receptor",
            "gene": "OXTR",
            "organism": "Homo sapiens",
            "function": "receptor",
        }
        with mock.patch.object(mechanism_mod, "fetch_uniprot", return_value=enriched):
            code, text = self._run("mechanism", "enrich", "--uniprot", "P30559")
            self.assertEqual(code, 0)
            self.assertIn("UniProt P30559", text)
            code, text = self._run("mechanism", "enrich", "--uniprot", "P30559", "--json")
            self.assertEqual(code, 0)

    def test_branchy_option_paths(self):
        out = self.base / "report.json"
        code, _ = self._run(
            "fulltext",
            "report",
            "--db",
            str(self.db),
            "--out",
            str(out),
            "--json",
        )
        self.assertEqual(code, 0)
        code, _ = self._run("db", "verify", "--db", str(self.db), "--sidecar", str(self.base / "absent.json"))
        self.assertEqual(code, 1)
        code, _ = self._run("mechanism", "check", "--db", str(self.db), "--json")
        self.assertEqual(code, 0)
        code, _ = self._run("mechanism", "check", "--db", str(self.db), "--holdout", "2")
        self.assertEqual(code, 2)
        code, _ = self._run(
            "mechanism",
            "simulate",
            "--db",
            str(self.db),
            "--entity",
            "NP:OXT",
            "--min-confidence",
            "2",
        )
        self.assertEqual(code, 2)
        code, _ = self._run("mechanism", "export", "--db", str(self.db))
        self.assertEqual(code, 0)
        code, _ = self._run(
            "power",
            "--test",
            "mean",
            "--effect-size",
            "0.5",
            "--out",
            str(self.base / "power.json"),
        )
        self.assertEqual(code, 0)
        draft = str(ROOT / "gold" / "v2" / "draft.jsonl")
        code, _ = self._run("gold", "validate", "--file", draft, "--json")
        self.assertEqual(code, 0)
        code, _ = self._run("gold", "agreement", "--a", draft, "--b", draft, "--effects")
        self.assertEqual(code, 0)

    def test_more_cli_branches(self):
        with (
            mock.patch.object(cli_mod, "run_ingest", return_value={"documents": 1}),
        ):
            code, _ = self._run(
                "ingest",
                "--query",
                "oxytocin",
                "--limit",
                "1",
                "--db",
                str(self.db),
                "--cache",
                str(self.base / "cache"),
                "--offline",
            )
        self.assertEqual(code, 0)
        out = self.base / "snapshot.db"
        self.assertEqual(cli_mod.main(["db", "backup", "--db", str(self.db), "--out", str(out)]), 0)
        code, text = self._run("db", "verify", "--db", str(out), "--json")
        self.assertEqual(code, 0)
        code, text = self._run(
            "db", "restore", "--backup", str(out), "--db", str(self.base / "restored.db"), "--json"
        )
        self.assertEqual(code, 0)
        frame = self.base / "frame.json"
        frame.write_text(
            json.dumps({"documents": [{"doc_id": "PMID:1", "stratum": "rct"}]}), encoding="utf-8"
        )
        draft = str(ROOT / "gold" / "v2" / "draft.jsonl")
        code, text = self._run("eval", "--db", str(self.db), "--gold", draft, "--frame", str(frame))
        self.assertEqual(code, 2)
        code, text = self._run(
            "eval",
            "--db",
            str(self.db),
            "--gold",
            draft,
            "--frame",
            str(frame),
            "--json",
        )
        self.assertEqual(code, 2)
        code, text = self._run("fulltext", "extract", "--db", str(self.db))
        self.assertEqual(code, 0)
        code, text = self._run("fulltext", "extract", "--db", str(self.db), "--json")
        self.assertEqual(code, 0)
        code, text = self._run("mechanism", "simulate", "--db", str(self.db), "--entity", "NP:OXT")
        self.assertEqual(code, 0)
        code, text = self._run("mechanism", "simulate", "--db", str(self.db), "--entity", "NP:OXT", "--json")
        self.assertEqual(code, 0)
        empty_enrich = {
            "accession": "P30559",
            "protein_name": "",
            "gene": "",
            "organism": "",
            "function": "",
        }
        with mock.patch.object(mechanism_mod, "fetch_uniprot", return_value=empty_enrich):
            code, text = self._run("mechanism", "enrich", "--uniprot", "P30559")
        self.assertEqual(code, 0)
        self.assertIn("protein: (unknown)", text)
        code, text = self._run("synthesize", "--db", str(self.db), "--json")
        self.assertEqual(code, 0)
        code, text = self._run("power", "--test", "mean", "--effect-size", "0.5")
        self.assertEqual(code, 0)
        code, text = self._run("power", "--test", "mean", "--effect-size", "0.5", "--json")
        self.assertEqual(code, 0)
        human = self.base / "human.jsonl"
        human.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "doc_id": "PMID:1",
                    "status": "human",
                    "mentions": [],
                    "claims": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        code, text = self._run("eval", "--db", str(self.db), "--gold", str(human), "--require-gold", "--json")
        self.assertEqual(code, 0)
        code, text = self._run("gold", "readiness", "--gold", str(human))
        self.assertEqual(code, 3)
        code, _ = self._run("ledger", "register", "--db", str(self.db), "--design", "rct")
        self.assertEqual(code, 2)
        primary = self.base / "primary.jsonl"
        primary.write_text(
            (ROOT / "gold" / "v2" / "draft.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
        )
        adjudications = self.base / "adj.jsonl"
        adjudications.write_text("", encoding="utf-8")
        code, _ = self._run(
            "gold",
            "merge",
            "--primary",
            str(primary),
            "--adjudications",
            str(adjudications),
            "--out",
            str(self.base / "merged.jsonl"),
        )
        self.assertEqual(code, 0)
        frame_short = self.base / "frame_short.json"
        frame_short.write_text(
            json.dumps({"documents": [{"doc_id": "PMID:NOPE", "stratum": "x"}]}), encoding="utf-8"
        )
        code, text = self._run("gold", "progress", "--gold", draft, "--frame", str(frame_short))
        self.assertEqual(code, 0)
        self.assertIn("outside frame:", text)

    def test_mocked_ingest_and_ablation_paths(self):
        with (
            mock.patch.object(cli_mod, "run_ingest", return_value={"documents": 1}),
            mock.patch.object(cli_mod.report, "render_all", return_value={}),
        ):
            code, text = self._run(
                "ingest",
                "--query",
                "oxytocin",
                "--limit",
                "1",
                "--db",
                str(self.db),
                "--cache",
                str(self.base / "cache"),
                "--offline",
                "--report",
                "--out",
                str(self.base / "reports"),
            )
        self.assertEqual(code, 0)
        self.assertIn("run:", text)
        with (
            mock.patch.object(cli_mod.corpus_mod, "load_corpus", return_value=([], [])),
            mock.patch.object(cli_mod.eval_mod, "load_gold", return_value=[]),
            mock.patch.object(cli_mod.ablate_mod, "run_ablation", return_value={"configs": []}),
            mock.patch.object(cli_mod.ablate_mod, "format_ablation", return_value="ablation"),
        ):
            code, text = self._run(
                "ablate",
                "--corpus",
                str(self.base / "corpus.json"),
                "--gold",
                str(self.base / "gold.jsonl"),
            )
        self.assertEqual(code, 0)
        self.assertIn("ablation", text)
        with (
            mock.patch.object(cli_mod.eval_mod, "load_gold", return_value=[]),
            mock.patch.object(cli_mod.corpus_mod, "load_corpus", return_value=([], [])),
            mock.patch.object(cli_mod.ablate_mod, "run_ablation", side_effect=ValueError("bad config")),
        ):
            code, _ = self._run("ablate", "--corpus", "c", "--gold", "g", "--configs", "bad")
        self.assertEqual(code, 2)
        with (
            mock.patch.object(cli_mod.eval_mod, "load_gold", return_value=[]),
            mock.patch.object(cli_mod.corpus_mod, "load_corpus", return_value=([], [])),
            mock.patch.object(
                cli_mod.ablate_mod, "run_ablation", side_effect=RuntimeError("missing fixture")
            ),
        ):
            code, _ = self._run("ablate", "--corpus", "c", "--gold", "g")
        self.assertEqual(code, 1)
        with (
            mock.patch.object(cli_mod.eval_mod, "load_gold", return_value=[]),
            mock.patch.object(cli_mod.corpus_mod, "load_corpus", return_value=([], [])),
            mock.patch.object(cli_mod.ablate_mod, "run_ablation", return_value={}),
        ):
            code, _ = self._run("ablate", "--corpus", "c", "--gold", "g", "--json")
        self.assertEqual(code, 0)

    def test_text_precursor_import_list_show_and_cleavage(self):
        path = self.base / "precursor.jsonl"
        path.write_text(
            json.dumps({"name": "test precursor", "sequence": "AKRPLG", "source": "unit-test"}) + "\n",
            encoding="utf-8",
        )
        code, text = self._run("precursor", "import", "--db", str(self.db), "--file", str(path))
        self.assertEqual(code, 0)
        code, text = self._run("precursor", "list", "--db", str(self.db))
        self.assertEqual(code, 0)
        self.assertIn("test precursor", text)
        store = Store(self.db)
        try:
            precursor_id = store.conn.execute("SELECT precursor_id FROM precursors LIMIT 1").fetchone()[
                "precursor_id"
            ]
        finally:
            store.close()
        code, text = self._run("precursor", "show", "--db", str(self.db), "--id", precursor_id)
        self.assertEqual(code, 0)
        self.assertIn("AKRPLG", text)
        code, text = self._run("precursor", "show", "--db", str(self.db), "--id", precursor_id, "--json")
        self.assertEqual(code, 0)
        code, text = self._run("precursor", "list", "--db", str(self.db), "--json")
        self.assertEqual(code, 0)
        code, text = self._run(
            "generate",
            "cleavage",
            "--db",
            str(self.db),
            "--precursor",
            precursor_id,
            "--enzyme",
            "trypsin",
        )
        self.assertEqual(code, 0)
        store = Store(self.db)
        try:
            candidate_id = store.conn.execute("SELECT candidate_id FROM candidates LIMIT 1").fetchone()[
                "candidate_id"
            ]
        finally:
            store.close()
        code, text = self._run(
            "generate",
            "analogs",
            "--db",
            str(self.db),
            "--candidate",
            candidate_id,
            "--json",
        )
        self.assertEqual(code, 0)


class TestDatabaseAdmin(unittest.TestCase):
    def _db(self, tmp: str) -> Path:
        db = Path(tmp) / "source.db"
        store = Store(db)
        try:
            store.add_document(Document(doc_id="PMID:1", abstract="Oxytocin increased trust."))
        finally:
            store.close()
        return db

    def test_backup_verify_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            out = Path(tmp) / "snapshot.db"
            payload = dbadmin_mod.backup(db, out)
            self.assertTrue(dbadmin_mod.sidecar_path(out).exists())
            self.assertEqual(payload["schema_version"], 8)
            self.assertTrue(dbadmin_mod.verify(out)["ok"])
            target = Path(tmp) / "restored.db"
            restored = dbadmin_mod.restore(out, target)
            self.assertEqual(restored["counts"]["documents"], 1)
            self.assertTrue(restored["verified"])
            with self.assertRaises(FileExistsError):
                dbadmin_mod.restore(out, target)
            dbadmin_mod.restore(out, target, force=True)
            self.assertTrue(dbadmin_mod.doctor(target)["ok"])

    def test_backup_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            out = Path(tmp) / "snapshot.db"
            first = dbadmin_mod.backup(db, out)
            with self.assertRaises(FileExistsError):
                dbadmin_mod.backup(db, out)
            second = dbadmin_mod.backup(db, out, force=True)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertTrue(second["copy_verified"])
            self.assertIn("source_sha256", second)

    def test_verify_detects_tampered_sidecar_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            out = Path(tmp) / "snapshot.db"
            dbadmin_mod.backup(db, out)
            sidecar = dbadmin_mod.sidecar_path(out)
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            payload["bytes"] = 1
            payload["schema"] = "wrong"
            sidecar.write_text(json.dumps(payload), encoding="utf-8")
            result = dbadmin_mod.verify(out)
            checks = {check["name"]: check["status"] for check in result["checks"]}
            self.assertFalse(result["ok"])
            self.assertEqual(checks["bytes"], "fail")
            self.assertEqual(checks["sidecar_schema"], "fail")

    def test_corrupt_backup_fails_verification_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            out = Path(tmp) / "snapshot.db"
            dbadmin_mod.backup(db, out)
            with open(out, "ab") as handle:
                handle.write(b"corrupt")
            self.assertFalse(dbadmin_mod.verify(out)["ok"])
            with self.assertRaises(ValueError):
                dbadmin_mod.restore(out, Path(tmp) / "restored.db")

    def test_missing_files_and_invalid_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                dbadmin_mod.backup(root / "absent.db", root / "out.db")
            self.assertFalse(dbadmin_mod.verify(root / "absent.db")["ok"])
            self.assertFalse(dbadmin_mod.doctor(root / "absent.db")["ok"])
            db = self._db(tmp)
            out = root / "snapshot.db"
            dbadmin_mod.backup(db, out)
            dbadmin_mod.sidecar_path(out).write_text("{bad", encoding="utf-8")
            self.assertFalse(dbadmin_mod.verify(out)["ok"])
            dbadmin_mod.sidecar_path(out).unlink()
            self.assertFalse(dbadmin_mod.verify(out)["ok"])
            with self.assertRaises(FileNotFoundError):
                dbadmin_mod.restore(root / "absent.db", root / "target.db")

    def test_backup_verify_restore_cli_text_and_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            out = Path(tmp) / "snapshot.db"
            target = Path(tmp) / "restored.db"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(cli_mod.main(["db", "backup", "--db", str(db), "--out", str(out)]), 0)
            self.assertIn("backup:", buffer.getvalue())
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                self.assertEqual(
                    cli_mod.main(["db", "backup", "--db", str(db), "--out", str(out)]),
                    cli_mod.EXIT_CONFIG,
                )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(
                    cli_mod.main(["db", "backup", "--db", str(db), "--out", str(out), "--force", "--json"]),
                    0,
                )
            self.assertIn("copy_verified", json.loads(buffer.getvalue()))
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(cli_mod.main(["db", "verify", "--db", str(out)]), 0)
            self.assertIn("sidecar_schema", buffer.getvalue())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(
                    cli_mod.main(["db", "restore", "--backup", str(out), "--db", str(target)]), 0
                )
            self.assertIn("restored:", buffer.getvalue())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(cli_mod.main(["doctor", "--db", str(target)]), 0)
            self.assertIn("ok:", buffer.getvalue())

    def test_doctor_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(tmp)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["doctor", "--db", str(db), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["ok"])


class TestModelDraft(unittest.TestCase):
    def test_draft_is_v3_and_schema_valid(self):
        records = annotation.load_records(ROOT / "gold" / "v2" / "draft.jsonl")
        self.assertEqual(len(records), 119)
        claims = [claim for record in records for claim in record["claims"]]
        self.assertGreater(len(claims), 0)
        self.assertTrue({claim["role"] for claim in claims} <= annotation.ROLES)
        for claim in claims:
            self.assertIn("section", claim)
            self.assertIn("evidence_start", claim)
            self.assertGreaterEqual(claim["evidence_start"], claim["start"])
            self.assertLessEqual(claim["evidence_end"], claim["end"])
        problems = [error for record in records for error in annotation.validate_record(record)]
        self.assertEqual(problems, [])

    @unittest.skipUnless((ROOT / "data" / "gold" / "corpus_v2.json").is_file(), "local corpus not present")
    def test_offline_replay_is_byte_identical(self):
        env = dict(os.environ, NTX_ROOT=str(ROOT), PYTHONPATH=str(ROOT / "src"))
        with tempfile.TemporaryDirectory() as tmp:
            outputs = []
            for name in ("a.jsonl", "b.jsonl"):
                target = Path(tmp) / name
                subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "build_gold_v2.py"),
                        "--from-corpus",
                        "--draft",
                        str(target),
                    ],
                    check=True,
                    capture_output=True,
                    env=env,
                )
                records = annotation.load_records(target)
                redacted = Path(tmp) / (name + ".redacted")
                annotation.write_records([annotation.redact_record(record) for record in records], redacted)
                outputs.append(redacted.read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], (ROOT / "gold" / "v2" / "draft.jsonl").read_bytes())


class TestGoldConflictsAndAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.a = self.base / "a.jsonl"
        self.b = self.base / "b.jsonl"
        base_record = {
            "schema_version": 2,
            "doc_id": "PMID:1",
            "annotator": "AB",
            "status": "human",
            "mentions": [],
            "claims": [
                {
                    "entity_id": "NP:OXT",
                    "start": 3,
                    "end": 30,
                    "sentence": "Oxytocin improved memory.",
                    "direction": "increase",
                    "outcome": "memory",
                    "role": "intervention",
                    "design": "rct",
                }
            ],
        }
        other = json.loads(json.dumps(base_record))
        other["annotator"] = "CD"
        other["claims"][0]["direction"] = "decrease"
        annotation.write_records([base_record], self.a)
        annotation.write_records([other], self.b)

    def tearDown(self):
        self.tmp.cleanup()

    def test_conflicts_skeleton_is_blank_and_merge_skips_blanks(self):
        out = self.base / "conflicts.jsonl"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(
                [
                    "gold",
                    "conflicts",
                    "--a",
                    str(self.a),
                    "--b",
                    str(self.b),
                    "--out",
                    str(out),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["conflicts"], 1)
        self.assertEqual(payload["by_field"], {"direction": 1})
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["value"], "")
        self.assertEqual(rows[0]["value_a"], "increase")
        self.assertEqual(rows[0]["value_b"], "decrease")

        records = annotation.load_records(self.a)
        annotation.apply_adjudications(records, rows)
        self.assertEqual(records[0]["claims"][0]["direction"], "increase")
        rows[0]["value"] = "decrease"
        annotation.apply_adjudications(records, rows)
        self.assertEqual(records[0]["claims"][0]["direction"], "decrease")

    def test_audit_sample_is_deterministic_and_stratified(self):
        db = self.base / "audit.db"
        store = Store(db)
        try:
            store.add_document(Document(doc_id="PMID:1", title="T", abstract="Oxytocin improved memory."))
            store.add_document(Document(doc_id="PMID:2", title="T", abstract="Oxytocin reduced anxiety."))
            store.add_claims(
                [
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="increase",
                        outcome="memory",
                        role="intervention",
                        design="rct",
                        sentence="Oxytocin improved memory.",
                        start=0,
                        end=24,
                        effect_type="smd",
                        effect_size=0.40,
                        effect_ci_low=0.10,
                        effect_ci_high=0.70,
                    ),
                    Claim(
                        doc_id="PMID:2",
                        entity_id="NP:OXT",
                        direction="decrease",
                        outcome="anxiety",
                        role="comparator",
                        design="animal",
                        sentence="Oxytocin reduced anxiety.",
                        start=0,
                        end=23,
                    ),
                ]
            )
        finally:
            store.close()
        store = Store(db)
        try:
            first = audit_mod.sample_claims(store, n=1, seed=7)
            second = audit_mod.sample_claims(store, n=1, seed=7)
            everything = audit_mod.sample_claims(store, n=99, seed=7)
            with self.assertRaises(ValueError):
                audit_mod.sample_claims(store, n=0)
        finally:
            store.close()
        self.assertEqual(first, second)
        self.assertEqual(len(everything), 2)
        self.assertEqual(len({(row["role"], row["design"]) for row in everything}), 2)
        worksheet = audit_mod.format_worksheet(everything)
        self.assertIn("verdict", worksheet)
        self.assertIn("Oxytocin improved memory.", worksheet)

    def test_audit_effects_only_and_effect_scoring(self):
        db = self.base / "audit_effects.db"
        store = Store(db)
        try:
            store.add_document(Document(doc_id="PMID:1", abstract="Oxytocin improved memory."))
            store.add_claims(
                [
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="increase",
                        outcome="memory",
                        sentence="Oxytocin improved memory.",
                        start=0,
                        end=24,
                        effect_type="smd",
                        effect_size=0.40,
                        effect_ci_low=0.10,
                        effect_ci_high=0.70,
                    )
                ]
            )
        finally:
            store.close()
        store = Store(db)
        try:
            rows = audit_mod.sample_claims(store, n=10, seed=0, effects_only=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["effect_type"], "smd")
            self.assertIn("effect verdict", audit_mod.format_worksheet(rows))
            result = audit_mod.score_verdicts(
                store, [{"claim_id": rows[0]["claim_id"], "verdict": "support", "effect_verdict": "correct"}]
            )
        finally:
            store.close()
        self.assertEqual(result["effects"]["sampled"], 1)
        self.assertEqual(result["effects"]["overall"]["n"], 1)
        self.assertEqual(result["effects"]["overall"]["precision"], 1.0)
        self.assertIn("effect audit", audit_mod.format_audit_score(result))

    def test_audit_cli_writes_worksheet(self):
        db = self.base / "audit_cli.db"
        store = Store(db)
        try:
            store.add_document(Document(doc_id="PMID:1", title="T", abstract="Oxytocin improved memory."))
            store.add_claims(
                [
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="increase",
                        outcome="memory",
                        role="intervention",
                        sentence="Oxytocin improved memory.",
                        start=0,
                        end=24,
                    )
                ]
            )
        finally:
            store.close()
        out = self.base / "worksheet.md"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(["audit", "sample", "--db", str(db), "--n", "5", "--out", str(out), "--json"])
        self.assertEqual(code, 0)
        rows = json.loads(buffer.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertIn("Claim audit worksheet", out.read_text(encoding="utf-8"))


class TestGoldIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = self.base / "integrity.db"
        self.abstract = "Oxytocin improved memory."
        self.start = 2  # Document.text() is title + "\n\n" + abstract
        store = Store(self.db)
        try:
            store.add_document(Document(doc_id="PMID:9", title="", abstract=self.abstract))
        finally:
            store.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _gold(self, sentence: str) -> Path:
        record = {
            "doc_id": "PMID:9",
            "mentions": [],
            "claims": [
                {
                    "entity_id": "NP:OXT",
                    "direction": "increase",
                    "outcome": "memory",
                    "start": self.start,
                    "sentence": sentence,
                }
            ],
        }
        path = self.base / ("gold_%d.jsonl" % len(sentence))
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        return path

    def test_valid_gold_passes_strict(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(
                [
                    "eval",
                    "--db",
                    str(self.db),
                    "--gold",
                    str(self._gold(self.abstract)),
                    "--strict-gold",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["aggregate"]["gold_span_errors"], 0)
        self.assertEqual(payload["aggregate"]["gold_missing_documents"], 0)

    def test_invalid_sentence_fails_strict(self):
        buffer = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = cli_mod.main(
                [
                    "eval",
                    "--db",
                    str(self.db),
                    "--gold",
                    str(self._gold("Oxytocin improved mood.")),
                    "--strict-gold",
                    "--json",
                ]
            )
        self.assertEqual(code, 1)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["aggregate"]["gold_span_errors"], 1)
        self.assertIn("gold integrity failed", errors.getvalue())


class TestAuditScore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_wilson_interval(self):
        low, high = stats.wilson_interval(8, 10)
        self.assertAlmostEqual(low, 0.490, places=3)
        self.assertAlmostEqual(high, 0.943, places=3)
        low, high = stats.wilson_interval(0, 10)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.278, places=3)

    def _db(self) -> Path:
        db = Path(self.tmp.name) / "score.db"
        store = Store(db)
        try:
            store.add_document(Document(doc_id="PMID:1", title="T", abstract="Oxytocin improved memory."))
            store.add_claims(
                [
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="increase",
                        outcome="memory",
                        role="intervention",
                        design="rct",
                        sentence="Oxytocin improved memory.",
                        start=3,
                        end=27,
                    ),
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="decrease",
                        outcome="anxiety",
                        role="intervention",
                        design="animal",
                        sentence="Oxytocin reduced anxiety.",
                        start=3,
                        end=26,
                    ),
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="increase",
                        outcome="social",
                        role="comparator",
                        design="animal",
                        sentence="Oxytocin helped social behavior.",
                        start=3,
                        end=33,
                    ),
                ]
            )
            self.ids = [
                row["claim_id"] for row in store.conn.execute("SELECT claim_id FROM claims ORDER BY claim_id")
            ]
        finally:
            store.close()
        return db

    def test_score_counts_missing_and_strata(self):
        db = self._db()
        verdicts = [
            {"claim_id": self.ids[0], "verdict": "support"},
            {"claim_id": self.ids[1], "verdict": "support"},
            {"claim_id": self.ids[2], "verdict": "refute"},
            {"claim_id": 9999, "verdict": "support"},
        ]
        store = Store(db)
        try:
            result = audit_mod.score_verdicts(store, verdicts)
        finally:
            store.close()
        self.assertEqual(result["sampled"], 4)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(result["overall"]["n"], 3)
        self.assertEqual(result["overall"]["support"], 2)
        self.assertEqual(result["overall"]["refute"], 1)
        self.assertAlmostEqual(result["overall"]["precision"], 0.667, places=3)
        self.assertEqual(set(result["by_role"]), {"intervention", "comparator"})
        text = audit_mod.format_audit_score(result)
        self.assertIn("Audit score", text)
        self.assertIn("precision", text)

    def test_score_counts_blank_and_unclear(self):
        db = self._db()
        store = Store(db)
        try:
            result = audit_mod.score_verdicts(
                store,
                [
                    {"claim_id": self.ids[0], "verdict": ""},
                    {"claim_id": self.ids[1], "verdict": "unclear"},
                    {"claim_id": self.ids[2], "verdict": "support"},
                ],
            )
        finally:
            store.close()
        self.assertEqual(result["blank"], 1)
        self.assertEqual(result["unclear"], 1)
        self.assertEqual(result["overall"]["n"], 1)
        self.assertEqual(result["overall"]["precision"], 1.0)

    def test_verdict_loader_rejects_bad_rows(self):
        bad = Path(self.tmp.name) / "bad.jsonl"
        bad.write_text(json.dumps({"claim_id": 1, "verdict": "maybe"}) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            audit_mod.load_verdicts(bad)
        duplicate = Path(self.tmp.name) / "dup.jsonl"
        duplicate.write_text(
            json.dumps({"claim_id": 1, "verdict": "support"})
            + "\n"
            + json.dumps({"claim_id": 1, "verdict": "refute"})
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            audit_mod.load_verdicts(duplicate)
        bad_effect = Path(self.tmp.name) / "bad_effect.jsonl"
        bad_effect.write_text(
            json.dumps({"claim_id": 1, "verdict": "support", "effect_verdict": "maybe"}) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            audit_mod.load_verdicts(bad_effect)

    def test_score_cli_json_and_template(self):
        db = self._db()
        template = Path(self.tmp.name) / "verdicts.jsonl"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(
                [
                    "audit",
                    "sample",
                    "--db",
                    str(db),
                    "--n",
                    "99",
                    "--verdicts-out",
                    str(template),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        rows = json.loads(buffer.getvalue())
        self.assertEqual(len(rows), 3)
        skeleton = [json.loads(line) for line in template.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(skeleton), 3)
        self.assertTrue(all(item["verdict"] == "" for item in skeleton))
        for item in skeleton:
            item["verdict"] = "support"
        verdicts = Path(self.tmp.name) / "filled.jsonl"
        verdicts.write_text("".join(json.dumps(item) + "\n" for item in skeleton), encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(["audit", "score", "--db", str(db), "--verdicts", str(verdicts), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["overall"]["n"], 3)
        self.assertEqual(payload["overall"]["precision"], 1.0)


class TestMechanismGraph(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _db(self, abstract: str, doc_id: str = "PMID:1") -> Path:
        db = self.base / (doc_id.replace(":", "_") + ".db")
        store = Store(db)
        try:
            process_documents(
                store,
                [Document(doc_id=doc_id, title="Mechanism study", abstract=abstract)],
                {},
            )
        finally:
            store.close()
        return db

    def _edges(self, db: Path) -> list[mechanism_mod.GraphEdge]:
        store = Store(db)
        try:
            _lexicon, edges = mechanism_mod.build_graph(store)
        finally:
            store.close()
        return edges

    def test_extract_entity_and_outcome_edges(self):
        db = self._db(
            "Oxytocin improved memory through OXTR activation. "
            "Oxytocin reduced anxiety through CREB activation."
        )
        edges = self._edges(db)
        direct = [edge for edge in edges if edge.source == "NP:OXT" and edge.target == "MECH:OXTR"]
        self.assertTrue(direct)
        self.assertEqual(direct[0].sign, 1)
        self.assertTrue(
            any(
                edge.source == "MECH:OXTR"
                and edge.target == "OUTCOME:memory"
                and edge.sign == 1
                and edge.entities == {"NP:OXT"}
                for edge in edges
            )
        )
        self.assertTrue(
            any(
                edge.source == "MECH:CREB" and edge.target == "OUTCOME:anxiety" and edge.sign == -1
                for edge in edges
            )
        )

    def test_simulation_and_check(self):
        db = self._db(
            "Oxytocin improved memory through OXTR activation. "
            "Oxytocin reduced anxiety through CREB activation."
        )
        store = Store(db)
        try:
            blocked = mechanism_mod.simulate(store, "NP:OXT", blocked=["MECH:OXTR"])
            check = mechanism_mod.check(store, holdout=0)
        finally:
            store.close()
        self.assertEqual(blocked["outcomes"]["memory"]["predicted_change"], "decrease")
        self.assertEqual(blocked["outcomes"]["anxiety"]["predicted_change"], "ambiguous")
        self.assertEqual(check["evaluated"], 2)
        self.assertEqual(check["agreement"], 1.0)
        text = mechanism_mod.format_simulation(blocked)
        self.assertIn("not a kinetic", text)

    def test_negation_and_receptor_name_overlap_are_skipped(self):
        db = self._db(
            "Oxytocin did not activate CREB in this assay. "
            "Oxytocin increased substance P receptor expression."
        )
        edges = self._edges(db)
        self.assertFalse(any(edge.source == "NP:OXT" and edge.target == "MECH:CREB" for edge in edges))
        self.assertFalse(any(edge.source == "NP:OXT" and edge.target == "MECH:TACR1" for edge in edges))

    def test_holdout_check_uses_only_held_claims(self):
        db = self.base / "holdout.db"
        store = Store(db)
        try:
            docs = [
                Document(
                    doc_id="PMID:%02d" % index,
                    title="Mechanism study",
                    abstract="Oxytocin improved memory through OXTR activation.",
                )
                for index in range(20)
            ]
            process_documents(store, docs, {})
            result = mechanism_mod.check(store, holdout=0.2)
        finally:
            store.close()
        self.assertEqual(result["mode"], "holdout")
        self.assertGreater(result["holdout_docs"], 0)
        self.assertEqual(result["holdout_docs"] + result["train_docs"], 20)
        self.assertEqual(result["evaluated"], result["holdout_docs"])
        self.assertEqual(result["agreement"], 1.0)

    def test_cli_json_export_and_edges(self):
        db = self._db("Oxytocin improved memory through OXTR activation.")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(
                [
                    "mechanism",
                    "simulate",
                    "--db",
                    str(db),
                    "--entity",
                    "NP:OXT",
                    "--block",
                    "MECH:OXTR",
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["outcomes"]["memory"]["predicted_change"], "decrease")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(["mechanism", "edges", "--db", str(db), "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(any(row["source"] == "NP:OXT" for row in json.loads(buffer.getvalue())))
        out = self.base / "graph.json"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(["mechanism", "export", "--db", str(db), "--out", str(out)])
        self.assertEqual(code, 0)
        graph = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(any(node["kind"] == "receptor" for node in graph["nodes"]))
        self.assertTrue(any(edge["origin"] == "prior" for edge in graph["edges"]))

    def test_link_only_edge_is_not_simulated(self):
        db = self._db("Oxytocin was associated with OXTR in the brain.")
        edges = self._edges(db)
        link_only = [
            edge
            for edge in edges
            if edge.source == "NP:OXT" and edge.target == "MECH:OXTR" and edge.sign == 0
        ]
        self.assertTrue(link_only)
        self.assertIn("No entity-mechanism sentences", mechanism_mod.format_edges([]))

    def test_check_without_mechanism_path_is_uncovered(self):
        db = self._db("Oxytocin improved memory in a randomized trial.")
        store = Store(db)
        try:
            result = mechanism_mod.check(store, holdout=0)
        finally:
            store.close()
        self.assertEqual(result["evaluated"], 0)
        self.assertEqual(result["uncovered"], 1)
        self.assertIsNone(result["agreement"])
        text = mechanism_mod.format_check(result)
        self.assertIn("uncovered: 1", text)

    def test_enrich_cli_json(self):
        payload = {
            "primaryAccession": "P30559",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "Oxytocin receptor"}}},
            "genes": [],
            "organism": {"scientificName": "Homo sapiens"},
            "comments": [],
        }
        buffer = io.StringIO()
        with (
            mock.patch.object(mechanism_mod.http, "get_json", return_value=payload),
            contextlib.redirect_stdout(buffer),
        ):
            code = cli_mod.main(
                [
                    "mechanism",
                    "enrich",
                    "--uniprot",
                    "P30559",
                    "--cache",
                    str(self.base / "cache"),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["protein_name"], "Oxytocin receptor")

    def test_unknown_entity_is_usage_error(self):
        db = self._db("Oxytocin improved memory through OXTR activation.")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = cli_mod.main(["mechanism", "simulate", "--db", str(db), "--entity", "NP:NOPE"])
        self.assertEqual(code, 2)
        self.assertIn("unknown entity", errors.getvalue())

    def test_uniprot_enrichment_flattening_and_offline(self):
        payload = {
            "primaryAccession": "P30559",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "Oxytocin receptor"}}},
            "genes": [{"geneName": {"value": "OXTR"}}],
            "organism": {"scientificName": "Homo sapiens"},
            "comments": [{"commentType": "FUNCTION", "texts": [{"value": "Receptor for oxytocin."}]}],
        }
        with mock.patch.object(mechanism_mod.http, "get_json", return_value=payload):
            result = mechanism_mod.fetch_uniprot("P30559", self.base / "cache")
        self.assertEqual(result["protein_name"], "Oxytocin receptor")
        self.assertEqual(result["gene"], "OXTR")
        self.assertEqual(result["function"], "Receptor for oxytocin.")
        with self.assertRaises(http_mod.OfflineError):
            mechanism_mod.fetch_uniprot("P30559", self.base / "empty-cache", offline=True)
        with self.assertRaises(ValueError):
            mechanism_mod.fetch_uniprot("not-an-accession!", self.base / "cache")


class TestEvaluationBreakdown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "eval.db"
        self.text = (
            "T\n\nOxytocin improved memory. Oxytocin reduced anxiety. Oxytocin helped social behavior."
        )
        sentences = [
            "Oxytocin improved memory.",
            "Oxytocin reduced anxiety.",
            "Oxytocin helped social behavior.",
        ]
        self.starts = [self.text.index(sentence) for sentence in sentences]
        store = Store(self.db)
        try:
            store.add_document(Document(doc_id="PMID:1", title="T", abstract=self.text[3:]))
            store.add_claims(
                [
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="increase",
                        outcome="memory",
                        role="intervention",
                        sentence=sentences[0],
                        start=self.starts[0],
                        end=self.starts[0] + len(sentences[0]),
                    ),
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="decrease",
                        outcome="anxiety",
                        role="intervention",
                        sentence=sentences[1],
                        start=self.starts[1],
                        end=self.starts[1] + len(sentences[1]),
                    ),
                    Claim(
                        doc_id="PMID:1",
                        entity_id="NP:OXT",
                        direction="increase",
                        outcome="social",
                        role="intervention",
                        sentence=sentences[2],
                        start=self.starts[2],
                        end=self.starts[2] + len(sentences[2]),
                    ),
                ]
            )
        finally:
            store.close()
        self.gold = [
            {
                "doc_id": "PMID:1",
                "mentions": [],
                "claims": [
                    {
                        "entity_id": "NP:OXT",
                        "direction": "increase",
                        "outcome": "memory",
                        "role": "intervention",
                        "start": self.starts[0],
                    },
                    {
                        "entity_id": "NP:OXT",
                        "direction": "decrease",
                        "outcome": "fear",
                        "role": "intervention",
                        "start": self.starts[1],
                    },
                    {
                        "entity_id": "NP:OXT",
                        "direction": "increase",
                        "outcome": "social",
                        "role": "comparator",
                        "start": self.starts[2],
                    },
                    {
                        "entity_id": "NP:NPY",
                        "direction": "increase",
                        "outcome": "memory",
                        "start": self.starts[0],
                    },
                ],
            }
        ]
        self.gold_path = Path(self.tmp.name) / "gold.jsonl"
        self.gold_path.write_text(json.dumps(self.gold[0]) + "\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_role_metrics_and_error_causes(self):
        store = Store(self.db)
        try:
            result = eval_mod.evaluate(store, self.gold)
        finally:
            store.close()
        self.assertEqual(result["claims_strict"], {"precision": 0.667, "recall": 0.5, "f1": 0.571})
        self.assertEqual(result["claims_role_strict"], {"precision": 0.333, "recall": 0.25, "f1": 0.286})
        self.assertEqual(result["role_accuracy_on_matched_claims"], 0.5)
        self.assertEqual(result["role_gold_coverage"], 0.75)
        self.assertEqual(result["role_mismatches"]["count"], 1)
        breakdown = result["claim_error_breakdown"]
        self.assertEqual(breakdown["false_negatives"]["outcome_mismatch"], 1)
        self.assertEqual(breakdown["false_negatives"]["entity_mismatch"], 1)
        self.assertEqual(breakdown["false_positives"]["outcome_mismatch"], 1)

    def test_effect_metrics_on_matched_claims(self):
        store = Store(self.db)
        try:
            store.conn.execute(
                "UPDATE claims SET effect_type=?, effect_size=?, effect_ci_low=?, effect_ci_high=?"
                " WHERE start=?",
                ("smd", 0.40, 0.10, 0.70, self.starts[0]),
            )
            store.conn.commit()
            gold = json.loads(json.dumps(self.gold))
            gold[0]["claims"][0].update(
                {
                    "effect_type": "smd",
                    "effect_size": 0.45,
                    "effect_ci_low": 0.15,
                    "effect_ci_high": 0.75,
                }
            )
            result = eval_mod.evaluate(store, gold)
        finally:
            store.close()
        self.assertEqual(result["claims_effect_strict"], {"precision": 1.0, "recall": 1.0, "f1": 1.0})
        self.assertEqual(result["effect_type_accuracy_on_matched_claims"], 1.0)
        self.assertAlmostEqual(result["effect_value_mae"], 0.05)
        self.assertEqual(result["effect_value_n"], 1)
        self.assertEqual(result["effect_ci_overlap_rate"], 1.0)
        self.assertAlmostEqual(result["effect_gold_coverage"], 0.25)
        self.assertAlmostEqual(result["effect_pred_coverage"], 0.333)

    def test_effect_mae_excludes_cross_type_pairs(self):
        store = Store(self.db)
        try:
            store.conn.execute(
                "UPDATE claims SET effect_type=?, effect_size=?, effect_ci_low=?, effect_ci_high=?"
                " WHERE start=?",
                ("smd", 0.40, 0.10, 0.70, self.starts[0]),
            )
            store.conn.execute(
                "UPDATE claims SET effect_type=?, effect_size=?, effect_ci_low=?, effect_ci_high=?"
                " WHERE start=?",
                ("odds_ratio", 1.40, 1.10, 1.80, self.starts[2]),
            )
            store.conn.commit()
            gold = json.loads(json.dumps(self.gold))
            gold[0]["claims"][0].update(
                {
                    "effect_type": "smd",
                    "effect_size": 0.45,
                    "effect_ci_low": 0.15,
                    "effect_ci_high": 0.75,
                }
            )
            gold[0]["claims"][2].update(
                {
                    "effect_type": "smd",
                    "effect_size": 0.90,
                    "effect_ci_low": 0.60,
                    "effect_ci_high": 1.20,
                }
            )
            result = eval_mod.evaluate(store, gold)
        finally:
            store.close()
        self.assertAlmostEqual(result["effect_value_mae"], 0.05)
        self.assertEqual(result["effect_value_n"], 1)
        self.assertEqual(result["effect_value_mae_by_type"], {"smd": {"n": 1, "mae": 0.05}})
        self.assertEqual(result["effect_value_cross_type_pairs"], 1)
        self.assertEqual(result["effect_type_accuracy_on_matched_claims"], 0.5)
        rendered = eval_mod.format_report(result)
        self.assertIn("same type only", rendered)
        self.assertIn("effect cross-type pairs", rendered)

    def test_require_gold_rejects_draft_or_unstatused_records(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli_mod.main(
                [
                    "eval",
                    "--db",
                    str(self.db),
                    "--gold",
                    str(self.gold_path),
                    "--require-gold",
                ]
            )
        self.assertEqual(code, 3)

    def test_effect_bootstrap_ci(self):
        store = Store(self.db)
        try:
            store.conn.execute(
                "UPDATE claims SET effect_type=?, effect_size=? WHERE start=?",
                ("smd", 0.40, self.starts[0]),
            )
            store.conn.commit()
            gold = json.loads(json.dumps(self.gold))
            gold[0]["claims"][0].update({"effect_type": "smd", "effect_size": 0.45})
            result = eval_mod.evaluate_with_ci(store, gold, n_boot=20)
        finally:
            store.close()
        self.assertIn("claims_effect_ci", result)
        self.assertIn("f1", result["claims_effect_ci"])

    def test_json_output_is_single_document(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_mod.main(
                [
                    "eval",
                    "--db",
                    str(self.db),
                    "--gold",
                    str(self.gold_path),
                    "--json",
                    "--bootstrap",
                    "20",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertIn("aggregate", payload)
        self.assertIn("claims_role_ci", payload)
        self.assertIn("claims_role_strict", payload["aggregate"])


class TestGoldProgress(unittest.TestCase):
    def _record(self, doc_id: str, direction: str = "increase") -> dict:
        return {
            "schema_version": 2,
            "doc_id": doc_id,
            "mentions": [],
            "claims": [
                {
                    "entity_id": "NP:OXT",
                    "start": 0,
                    "end": 10,
                    "direction": direction,
                    "outcome": "memory",
                    "role": "intervention",
                }
            ],
        }

    def test_progress_against_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame.json"
            frame.write_text(
                json.dumps(
                    {
                        "documents": [
                            {"doc_id": "PMID:1", "stratum": "human_trial"},
                            {"doc_id": "PMID:2", "stratum": "preclinical"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            gold = Path(tmp) / "gold.jsonl"
            gold.write_text(json.dumps(self._record("PMID:1")) + "\n", encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    ["gold", "progress", "--gold", str(gold), "--frame", str(frame), "--json"]
                )
            payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["counts"]["frame_documents"], 2)
        self.assertEqual(payload["counts"]["annotated_in_frame"], 1)
        self.assertEqual(payload["counts"]["missing_documents"], 1)
        self.assertEqual(payload["missing"], ["PMID:2"])
        self.assertIn("human_trial", payload["by_stratum"])

    def test_diff_reports_one_sided_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = Path(tmp) / "a.jsonl"
            right = Path(tmp) / "b.jsonl"
            left.write_text(json.dumps(self._record("PMID:1")) + "\n", encoding="utf-8")
            right.write_text(
                json.dumps(self._record("PMID:1"))
                + "\n"
                + json.dumps(self._record("PMID:1", direction="decrease"))
                + "\n",
                encoding="utf-8",
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["gold", "diff", "--a", str(left), "--b", str(right), "--json"])
            payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["claims_only_in_a"], 0)
        self.assertEqual(payload["claims_only_in_b"], 1)

    def test_progress_text_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame.json"
            frame.write_text(
                json.dumps({"documents": [{"doc_id": "PMID:1", "stratum": "human_trial"}]}),
                encoding="utf-8",
            )
            gold = Path(tmp) / "gold.jsonl"
            gold.write_text(json.dumps(self._record("PMID:1")) + "\n", encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["gold", "progress", "--gold", str(gold), "--frame", str(frame)])
        self.assertEqual(code, 0)
        self.assertIn("| Stratum | Documents | Annotated | Claims |", buffer.getvalue())
        self.assertIn("frame documents:      1", buffer.getvalue())


class TestGoldReadiness(unittest.TestCase):
    @staticmethod
    def _record(doc_id: str, status: str) -> dict[str, Any]:
        return {"schema_version": 2, "doc_id": doc_id, "status": status, "mentions": [], "claims": []}

    def test_draft_is_not_ready(self):
        frame = [{"doc_id": "PMID:1", "stratum": "human_trial"}]
        payload = eval_mod.gold_readiness([self._record("PMID:1", "draft")], frame)
        self.assertFalse(payload["ready"])
        self.assertIn("non_human_records", payload["blockers"])
        self.assertIn("double_annotation_missing", payload["blockers"])
        self.assertEqual(payload["counts"]["draft_documents"], 1)

    def test_complete_human_double_annotation_is_ready(self):
        frame = [{"doc_id": "PMID:%d" % index, "stratum": "human_trial"} for index in range(30)]
        gold = [self._record("PMID:%d" % index, "human") for index in range(30)]
        second = [self._record("PMID:%d" % index, "human") for index in range(30)]
        payload = eval_mod.gold_readiness(gold, frame, second)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["blockers"], [])
        self.assertEqual(payload["counts"]["double_annotated_documents"], 30)

    def test_missing_and_outside_frame_are_blockers(self):
        empty = eval_mod.gold_readiness([], [{"doc_id": "PMID:1"}])
        self.assertIn("no_gold_records", empty["blockers"])
        self.assertIn("incomplete_frame", empty["blockers"])
        outside = eval_mod.gold_readiness(
            [self._record("PMID:2", "human")],
            [{"doc_id": "PMID:1"}],
        )
        self.assertIn("documents_outside_frame", outside["blockers"])
        self.assertEqual(outside["counts"]["gold_documents"], 1)

    def test_cli_readiness_exits_three_for_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame.json"
            frame.write_text(json.dumps({"documents": [{"doc_id": "PMID:1"}]}), encoding="utf-8")
            gold = Path(tmp) / "gold.jsonl"
            gold.write_text(json.dumps(self._record("PMID:1", "draft")) + "\n", encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    ["gold", "readiness", "--gold", str(gold), "--frame", str(frame), "--json"]
                )
        self.assertEqual(code, 3)
        payload = json.loads(buffer.getvalue())
        self.assertFalse(payload["ready"])
        self.assertIn("non_human_records", payload["blockers"])


class TestConfigCommand(unittest.TestCase):
    def test_config_command_prints_effective_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ntx.toml"
            path.write_text("[server]\nport = 9123\n", encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["--config", str(path), "config"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["port"], 9123)

    def test_invalid_config_returns_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ntx.toml"
            path.write_text("[server]\nport = 0\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli_mod.main(["--config", str(path), "check", "--db", str(path)])
        self.assertEqual(code, 3)
        self.assertIn("NTX_PORT", stderr.getvalue())


class TestLedgerSchema(unittest.TestCase):
    def _candidate(self) -> Candidate:
        return Candidate(
            candidate_id="C-test",
            name="test peptide",
            sequence="ACDE",
            length=4,
            properties={"heuristic": True},
            created_at=clock.iso(),
        )

    def test_schema_v4_creates_ledger_tables_and_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "ledger.db")
            try:
                self.assertEqual(store.schema_version, SCHEMA_VERSION)
                tables = {
                    row[0] for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertTrue({"candidates", "experiments", "predictions", "outcomes"} <= tables)
                triggers = {
                    row[0]
                    for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
                }
                self.assertIn("candidates_no_update", triggers)
                self.assertIn("outcomes_no_delete", triggers)
            finally:
                store.close()

    def test_v3_database_migrates_without_losing_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mig4.db"
            store = Store(path)
            try:
                doc = Document(doc_id="PMID:M4", title="Oxytocin", abstract="Oxytocin improved memory.")
                process_documents(store, [doc])
                before = store.counts()["claims"]
                store.conn.executescript(
                    "DROP TABLE predictions;"
                    "DROP TABLE outcomes;"
                    "DROP TABLE experiments;"
                    "DROP TABLE candidates;"
                    "PRAGMA user_version=3;"
                )
                store.conn.commit()
            finally:
                store.close()
            upgraded = Store(path)
            try:
                self.assertEqual(upgraded.schema_version, SCHEMA_VERSION)
                self.assertEqual(upgraded.counts()["claims"], before)
                upgraded.add_candidate(self._candidate())
                self.assertEqual(upgraded.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 1)
            finally:
                upgraded.close()

    def test_v5_database_migrates_power_and_source_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mig6.db"
            store = Store(path)
            try:
                store.conn.execute(
                    "INSERT INTO experiments (experiment_id, hypothesis_id) VALUES ('E-old', 'H-old')"
                )
                store.conn.execute(
                    "INSERT INTO outcomes (outcome_id, experiment_id, observed_direction)"
                    " VALUES ('O-old', 'E-old', 'increase')"
                )
                store.conn.commit()
                store.conn.executescript(
                    "DROP INDEX idx_outcomes_source;"
                    "ALTER TABLE outcomes DROP COLUMN source_id;"
                    "ALTER TABLE experiments DROP COLUMN power_json;"
                    "PRAGMA user_version=5;"
                )
                store.conn.commit()
            finally:
                store.close()
            upgraded = Store(path)
            try:
                self.assertEqual(upgraded.schema_version, SCHEMA_VERSION)
                experiment_columns = {
                    row[1] for row in upgraded.conn.execute("PRAGMA table_info(experiments)")
                }
                outcome_columns = {row[1] for row in upgraded.conn.execute("PRAGMA table_info(outcomes)")}
                self.assertIn("power_json", experiment_columns)
                self.assertIn("source_id", outcome_columns)
                indexes = {row[1] for row in upgraded.conn.execute("PRAGMA index_list(outcomes)")}
                self.assertIn("idx_outcomes_source", indexes)
                self.assertEqual(
                    upgraded.conn.execute(
                        "SELECT power_json FROM experiments WHERE experiment_id='E-old'"
                    ).fetchone()[0],
                    "{}",
                )
                self.assertEqual(
                    upgraded.conn.execute(
                        "SELECT source_id FROM outcomes WHERE outcome_id='O-old'"
                    ).fetchone()[0],
                    "",
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    upgraded.conn.execute("UPDATE outcomes SET qc='fail' WHERE outcome_id='O-old'")
                upgraded.conn.rollback()
            finally:
                upgraded.close()

    def test_ledger_rows_are_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "append.db")
            try:
                store.add_candidate(self._candidate())
                with self.assertRaises(sqlite3.IntegrityError):
                    store.conn.execute("UPDATE candidates SET name='changed' WHERE candidate_id='C-test'")
                with self.assertRaises(sqlite3.IntegrityError):
                    store.conn.execute("DELETE FROM candidates WHERE candidate_id='C-test'")
                store.conn.rollback()
                self.assertEqual(
                    store.conn.execute("SELECT name FROM candidates WHERE candidate_id='C-test'").fetchone()[
                        0
                    ],
                    "test peptide",
                )
            finally:
                store.close()


class TestCandidates(unittest.TestCase):
    def _write(self, tmp: str, records: list[dict]) -> Path:
        path = Path(tmp) / "candidates.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def test_sequence_validation_normalizes_and_rejects_bad_input(self):
        self.assertEqual(candidates_mod.validate_sequence(" acde\n"), "ACDE")
        with self.assertRaises(candidates_mod.CandidateError):
            candidates_mod.validate_sequence("AC1D")
        with self.assertRaises(candidates_mod.CandidateError):
            candidates_mod.validate_sequence("AC DE")
        with self.assertRaises(candidates_mod.CandidateError):
            candidates_mod.validate_sequence("A")

    def test_properties_are_heuristic_and_match_reference_values(self):
        props = candidates_mod.compute_properties("ACDE")
        self.assertEqual(props["length"], 4)
        self.assertAlmostEqual(props["molecular_weight"], 436.1264, places=3)
        self.assertAlmostEqual(props["net_charge_pH7_4"], -2.15, places=2)
        self.assertAlmostEqual(props["gravy"], -0.675, places=3)
        self.assertEqual(props["aromatic_count"], 0)
        self.assertEqual(props["cysteine_count"], 1)
        self.assertFalse(props["protease_motifs"]["dpp4_like"])
        self.assertTrue(props["heuristic"])
        self.assertFalse(props["modifications_applied_to_properties"])

    def test_import_is_transactional_and_reports_line_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                [
                    {"name": "one", "sequence": "ACDE", "target_entity_id": "NP:OXT"},
                    {"name": "two", "sequence": "KLRY"},
                ],
            )
            store = Store(Path(tmp) / "cand.db")
            try:
                result = candidates_mod.import_candidates(store, path)
                self.assertEqual(result["imported"], 2)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 2)
                with self.assertRaises(candidates_mod.CandidateError):
                    candidates_mod.import_candidates(store, path)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 2)
            finally:
                store.close()

            bad = self._write(
                tmp,
                [
                    {"name": "ok", "sequence": "ACDE"},
                    {"name": "bad", "sequence": "AC1D"},
                ],
            )
            store = Store(Path(tmp) / "bad.db")
            try:
                with self.assertRaises(candidates_mod.CandidateError) as ctx:
                    candidates_mod.import_candidates(store, bad)
                self.assertIn("line 2", str(ctx.exception))
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)
            finally:
                store.close()

    def test_unknown_target_entity_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, [{"name": "x", "sequence": "ACDE", "target_entity_id": "NP:NOPE"}])
            store = Store(Path(tmp) / "entity.db")
            try:
                with self.assertRaises(candidates_mod.CandidateError):
                    candidates_mod.import_candidates(store, path)
            finally:
                store.close()

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, [{"name": "one", "sequence": "ACDE"}])
            store = Store(Path(tmp) / "dry.db")
            try:
                result = candidates_mod.import_candidates(store, path, dry_run=True)
                self.assertTrue(result["dry_run"])
                self.assertEqual(len(result["candidates"]), 1)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)
            finally:
                store.close()

    def test_candidate_cli_import_list_and_show_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "cli.db"
            path = self._write(tmp, [{"name": "one", "sequence": "ACDE", "target_entity_id": "NP:OXT"}])
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["candidate", "import", "--db", str(db), "--file", str(path), "--json"])
            self.assertEqual(code, 0)
            imported = json.loads(buffer.getvalue())
            candidate_id = imported["candidates"][0]["candidate_id"]

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["candidate", "list", "--db", str(db), "--json"])
            self.assertEqual(code, 0)
            listed = json.loads(buffer.getvalue())
            self.assertEqual(listed["candidates"][0]["candidate_id"], candidate_id)

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["candidate", "show", "--db", str(db), "--id", candidate_id, "--json"])
            self.assertEqual(code, 0)
            shown = json.loads(buffer.getvalue())
            self.assertEqual(shown["name"], "one")
            self.assertTrue(shown["properties"]["heuristic"])


class TestPredictionLedger(unittest.TestCase):
    def _store(self, tmp: str):
        store = Store(Path(tmp) / "ledger.db")
        for index, direction in enumerate(("increase", "decrease"), start=1):
            sentence = "Oxytocin %s memory in adults." % (
                "improved" if direction == "increase" else "impaired"
            )
            doc = Document(doc_id="PMID:L%d" % index, title="T", abstract=sentence)
            store.add_document(doc)
            text = doc.text()
            start = text.index(sentence)
            store.add_claims(
                [
                    Claim(
                        doc_id=doc.doc_id,
                        entity_id="NP:OXT",
                        direction=direction,
                        outcome="memory",
                        sentence=sentence,
                        start=start,
                        end=start + len(sentence),
                        evidence_start=start,
                        evidence_end=start + len(sentence),
                    )
                ]
            )
        hypotheses = discovery.generate(store, top=100, include_untested=False)
        hypothesis = next(item for item in hypotheses if item.kind == "contradiction")
        return store, hypothesis

    def test_register_snapshots_hypothesis_and_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                result = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="memory task",
                    primary_outcome="memory",
                    n_planned=40,
                    preregistration="doi:10.0000/example",
                )
                self.assertEqual(result["experiment"]["hypothesis_id"], hypothesis.hypothesis_id)
                self.assertEqual(result["experiment"]["hypothesis_kind"], "contradiction")
                self.assertEqual(result["prediction"]["direction"], "increase")
                self.assertEqual(result["prediction"]["probability"], 0.7)
                self.assertEqual(result["experiment"]["n_planned"], 40)
            finally:
                store.close()

    def test_register_rejects_bad_inputs_and_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.register(
                        store,
                        hypothesis_id="H-nope",
                        prediction="increase",
                        probability=0.7,
                        design="rct",
                        assay="a",
                        primary_outcome="memory",
                    )
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.register(
                        store,
                        hypothesis_id=hypothesis.hypothesis_id,
                        prediction="increase",
                        probability=1.0,
                        design="rct",
                        assay="a",
                        primary_outcome="memory",
                    )
                with mock.patch.dict(os.environ, {clock.FROZEN_ENV: "2026-01-01T00:00:00+00:00"}):
                    first = ledger_mod.register(
                        store,
                        hypothesis_id=hypothesis.hypothesis_id,
                        prediction="increase",
                        probability=0.7,
                        design="rct",
                        assay="a",
                        primary_outcome="memory",
                    )
                with (
                    mock.patch.dict(os.environ, {clock.FROZEN_ENV: "2026-01-01T00:00:00+00:00"}),
                    self.assertRaises(ledger_mod.LedgerError),
                ):
                    ledger_mod.register(
                        store,
                        hypothesis_id=hypothesis.hypothesis_id,
                        prediction="increase",
                        probability=0.7,
                        design="rct",
                        assay="a",
                        primary_outcome="memory",
                    )
                self.assertTrue(first["experiment"]["experiment_id"].startswith("E-"))
            finally:
                store.close()

    def test_outcome_validation_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                result = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="decrease",
                    probability=0.8,
                    design="animal",
                    assay="a",
                    primary_outcome="memory",
                )
                experiment_id = result["experiment"]["experiment_id"]
                before = ledger_mod.status(store)
                self.assertEqual(before["experiments"], 1)
                self.assertEqual(before["pending"], 1)

                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.record_outcome(
                        store,
                        experiment_id=experiment_id,
                        observed_direction="decrease",
                        ci_low=1.0,
                        ci_high=0.5,
                        n=20,
                    )
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.record_outcome(store, experiment_id="E-nope", observed_direction="decrease")
                ledger_mod.record_outcome(
                    store,
                    experiment_id=experiment_id,
                    observed_direction="decrease",
                    effect_size=-0.4,
                    ci_low=-0.7,
                    ci_high=-0.1,
                    n=20,
                    qc="pass",
                )
                after = ledger_mod.status(store)
                self.assertEqual(after["pending"], 0)
                self.assertEqual(after["eligible"], 1)
                self.assertEqual(after["outcomes"], 1)
            finally:
                store.close()

    def test_export_content_hash_ignores_export_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="a",
                    primary_outcome="memory",
                )
                first = ledger_mod.export_ledger(store)
                second = ledger_mod.export_ledger(store)
                self.assertEqual(first["content_hash"], second["content_hash"])
                self.assertIn("exported_at", first)
                self.assertEqual(first["experiments"][0]["hypothesis_snapshot"]["kind"], "contradiction")
            finally:
                store.close()

    def test_ledger_cli_register_status_and_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            store.close()
            db = str(Path(tmp) / "ledger.db")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "ledger",
                        "register",
                        "--db",
                        db,
                        "--hypothesis",
                        hypothesis.hypothesis_id,
                        "--design",
                        "rct",
                        "--assay",
                        "task",
                        "--primary-outcome",
                        "memory",
                        "--prediction",
                        "increase",
                        "--probability",
                        "0.7",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            registered = json.loads(buffer.getvalue())
            experiment_id = registered["experiment"]["experiment_id"]

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["ledger", "status", "--db", db, "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buffer.getvalue())["pending"], 1)

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "ledger",
                        "outcome",
                        "--db",
                        db,
                        "--experiment",
                        experiment_id,
                        "--observed",
                        "increase",
                        "--n",
                        "20",
                        "--qc",
                        "pass",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buffer.getvalue())["outcome"]["observed_direction"], "increase")

    def _second_checkpoint(self, tmp: str) -> Path:
        chain = Path(tmp) / "chain2"
        chain.mkdir(parents=True, exist_ok=True)
        return chain / "checkpoint.json"

    def test_ledger_cli_text_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            db = str(Path(tmp) / "ledger.db")
            store.close()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "ledger",
                        "register",
                        "--db",
                        db,
                        "--hypothesis",
                        hypothesis.hypothesis_id,
                        "--design",
                        "rct",
                        "--assay",
                        "task",
                        "--primary-outcome",
                        "memory",
                        "--prediction",
                        "increase",
                        "--probability",
                        "0.7",
                        "--dry-run",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("dry run: no rows written", buffer.getvalue())
            self.assertIn("power: not supplied", buffer.getvalue())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "ledger",
                        "register",
                        "--db",
                        db,
                        "--hypothesis",
                        hypothesis.hypothesis_id,
                        "--design",
                        "rct",
                        "--assay",
                        "task",
                        "--primary-outcome",
                        "memory",
                        "--prediction",
                        "increase",
                        "--probability",
                        "0.7",
                    ]
                )
            self.assertEqual(code, 0)
            store = Store(Path(db))
            try:
                experiment_id = store.conn.execute(
                    "SELECT experiment_id FROM experiments LIMIT 1"
                ).fetchone()["experiment_id"]
            finally:
                store.close()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "ledger",
                        "outcome",
                        "--db",
                        db,
                        "--experiment",
                        experiment_id,
                        "--observed",
                        "increase",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("outcome:", buffer.getvalue())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(cli_mod.main(["ledger", "export", "--db", db]), 0)
            self.assertIn("content_hash", json.loads(buffer.getvalue()))
            checkpoint = Path(tmp) / "checkpoint.json"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(
                    cli_mod.main(["ledger", "checkpoint", "--db", db, "--out", str(checkpoint)]),
                    0,
                )
            self.assertIn("checkpoint", buffer.getvalue().lower())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(
                    cli_mod.main(
                        [
                            "ledger",
                            "checkpoint",
                            "--db",
                            db,
                            "--out",
                            str(self._second_checkpoint(tmp)),
                            "--json",
                        ]
                    ),
                    0,
                )
            self.assertIn("content_hash", json.loads(buffer.getvalue()))
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(
                    cli_mod.main(
                        [
                            "ledger",
                            "verify-checkpoints",
                            "--db",
                            db,
                            "--dir",
                            str(Path(tmp)),
                        ]
                    ),
                    0,
                )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(
                    cli_mod.main(
                        [
                            "ledger",
                            "verify-checkpoints",
                            "--db",
                            db,
                            "--dir",
                            str(Path(tmp)),
                            "--json",
                        ]
                    ),
                    0,
                )


class TestLedgerScoring(TestPredictionLedger):
    def _register(self, store, hypothesis, *, design, prediction, probability):
        return ledger_mod.register(
            store,
            hypothesis_id=hypothesis.hypothesis_id,
            prediction=prediction,
            probability=probability,
            design=design,
            assay="assay",
            primary_outcome="memory",
        )["experiment"]["experiment_id"]

    def test_hand_computed_directional_brier_and_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                first = self._register(
                    store, hypothesis, design="rct", prediction="increase", probability=0.8
                )
                second = self._register(
                    store, hypothesis, design="animal", prediction="decrease", probability=0.6
                )
                ledger_mod.record_outcome(store, experiment_id=first, observed_direction="increase", n=30)
                ledger_mod.record_outcome(store, experiment_id=second, observed_direction="increase", n=20)
                third = self._register(
                    store, hypothesis, design="in_vitro", prediction="increase", probability=0.7
                )
                ledger_mod.record_outcome(
                    store,
                    experiment_id=third,
                    observed_direction="increase",
                    qc="fail",
                    n=6,
                )
                fourth = self._register(
                    store, hypothesis, design="cohort", prediction="decrease", probability=0.6
                )
                ledger_mod.record_outcome(store, experiment_id=fourth, observed_direction="none", n=50)

                result = ledger_mod.score(store)
                self.assertEqual(result["n"], 2)
                self.assertEqual(result["pending"], 0)
                self.assertEqual(result["excluded"], 2)
                self.assertAlmostEqual(result["brier"], 0.20, places=4)
                self.assertAlmostEqual(result["brier_skill"], 0.20, places=4)
                self.assertAlmostEqual(result["accuracy"], 0.50, places=4)
                self.assertTrue(result["underpowered"])
                self.assertIn("underpowered", result["warning"])
                self.assertEqual(result["by_kind"]["contradiction"]["n"], 2)
                self.assertIsNone(result["by_kind"]["contradiction"].get("brier"))
            finally:
                store.close()

    def test_latest_outcome_revision_is_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                experiment_id = self._register(
                    store, hypothesis, design="rct", prediction="increase", probability=0.8
                )
                ledger_mod.record_outcome(
                    store, experiment_id=experiment_id, observed_direction="increase", n=30
                )
                ledger_mod.record_outcome(
                    store, experiment_id=experiment_id, observed_direction="decrease", n=32
                )
                result = ledger_mod.score(store)
                self.assertEqual(result["n"], 1)
                self.assertAlmostEqual(result["brier"], 0.64, places=4)
                self.assertEqual(result["accuracy"], 0.0)
            finally:
                store.close()

    def test_empty_score_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "empty.db")
            try:
                result = ledger_mod.score(store)
                self.assertEqual(result["n"], 0)
                self.assertIsNone(result["brier"])
                self.assertIn("no eligible observations", result["warning"])
            finally:
                store.close()

    def test_valid_candidate_can_be_linked_to_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                store.add_candidate(
                    Candidate(
                        candidate_id="C-link",
                        name="test",
                        sequence="ACDE",
                        length=4,
                        properties={"heuristic": True},
                        created_at=clock.iso(),
                    )
                )
                result = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    candidate_id="C-link",
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="a",
                    primary_outcome="memory",
                )
                self.assertEqual(result["experiment"]["candidate_id"], "C-link")
            finally:
                store.close()

    def test_score_cli_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                experiment_id = self._register(
                    store, hypothesis, design="rct", prediction="increase", probability=0.8
                )
                ledger_mod.record_outcome(
                    store, experiment_id=experiment_id, observed_direction="increase", n=30
                )
            finally:
                store.close()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["ledger", "score", "--db", str(Path(tmp) / "ledger.db"), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertAlmostEqual(payload["brier"], 0.04, places=4)
            self.assertTrue(payload["underpowered"])


class TestCandidateInputsAndFormatting(unittest.TestCase):
    def test_validation_and_file_error_branches(self):
        for value in ("", "A" * 201):
            with self.assertRaises(candidates_mod.CandidateError):
                candidates_mod.validate_sequence(value)
        with self.assertRaises(candidates_mod.CandidateError):
            candidates_mod.validate_sequence(7)
        self.assertEqual(
            candidates_mod.candidate_id_for("ACDE", [], ""),
            candidates_mod.candidate_id_for("ACDE", [], ""),
        )

        bad_records = [
            [],
            {"sequence": "ACDE"},
            {"name": "", "sequence": "ACDE"},
            {"name": "x"},
            {"name": "x", "sequence": "ACDE", "modifications": "acetyl"},
            {"name": "x", "sequence": "ACDE", "target_entity_id": 4},
            {"name": "x", "sequence": "ACDE", "hypothesis_id": 4},
            {"name": "x", "sequence": "ACDE", "provenance": ""},
            {"name": "x", "sequence": "ACDE", "notes": 4},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "bad.db")
            try:
                for index, record in enumerate(bad_records):
                    path = Path(tmp) / ("bad%d.jsonl" % index)
                    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
                    with self.assertRaises(candidates_mod.CandidateError):
                        candidates_mod.import_candidates(store, path)
            finally:
                store.close()

            malformed = Path(tmp) / "bad.json"
            malformed.write_text("{not json", encoding="utf-8")
            store = Store(Path(tmp) / "bad2.db")
            try:
                with self.assertRaises(candidates_mod.CandidateError):
                    candidates_mod.import_candidates(store, malformed)
                not_array = Path(tmp) / "object.json"
                not_array.write_text(json.dumps({"name": "x"}), encoding="utf-8")
                with self.assertRaises(candidates_mod.CandidateError):
                    candidates_mod.import_candidates(store, not_array)
                missing = Path(tmp) / "missing.jsonl"
                with self.assertRaises(candidates_mod.CandidateError):
                    candidates_mod.import_candidates(store, missing)
                binary = Path(tmp) / "binary.jsonl"
                binary.write_bytes(b"\xff\xfe")
                with self.assertRaises(candidates_mod.CandidateError):
                    candidates_mod.import_candidates(store, binary)
            finally:
                store.close()

    def test_json_array_import_and_human_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.json"
            path.write_text(
                json.dumps([{"name": "one", "sequence": "ACDE", "modifications": ["acetyl"]}]),
                encoding="utf-8",
            )
            store = Store(Path(tmp) / "array.db")
            try:
                result = candidates_mod.import_candidates(store, path)
                self.assertEqual(result["imported"], 1)
                rows = candidates_mod.candidate_rows(store)
                self.assertIn("one", candidates_mod.format_candidates(rows))
                self.assertIn("modifications: acetyl", candidates_mod.format_candidate(rows[0]))
                self.assertEqual(candidates_mod.format_candidates([]), "no candidates in this database")
                self.assertIsNone(candidates_mod.get_candidate(store, "C-nope"))
            finally:
                store.close()

    def test_duplicate_inside_file_is_rejected_without_partial_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dup.jsonl"
            record = {"name": "one", "sequence": "ACDE"}
            path.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n", encoding="utf-8")
            store = Store(Path(tmp) / "dup.db")
            try:
                with self.assertRaises(candidates_mod.CandidateError):
                    candidates_mod.import_candidates(store, path)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)
            finally:
                store.close()

    def test_utf8_bom_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bom.jsonl"
            path.write_text(
                "\ufeff" + json.dumps({"name": "one", "sequence": "ACDE"}) + "\n",
                encoding="utf-8",
            )
            store = Store(Path(tmp) / "bom.db")
            try:
                result = candidates_mod.import_candidates(store, path)
                self.assertEqual(result["imported"], 1)
            finally:
                store.close()

    def test_candidate_human_cli_and_missing_show(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "human.db"
            path = Path(tmp) / "one.jsonl"
            path.write_text(json.dumps({"name": "one", "sequence": "ACDE"}) + "\n", encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["candidate", "import", "--db", str(db), "--file", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("imported 1 candidate", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["candidate", "list", "--db", str(db)])
            self.assertEqual(code, 0)
            self.assertIn("candidate", buffer.getvalue())

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli_mod.main(["candidate", "show", "--db", str(db), "--id", "C-nope"])
            self.assertEqual(code, 2)
            self.assertIn("not found", stderr.getvalue())


class TestLedgerInputsAndFormatting(TestPredictionLedger):
    def test_register_validation_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            base = {
                "hypothesis_id": hypothesis.hypothesis_id,
                "prediction": "increase",
                "probability": 0.7,
                "design": "rct",
                "assay": "a",
                "primary_outcome": "memory",
            }
            try:
                for update in (
                    {"prediction": "none"},
                    {"probability": 0.4},
                    {"probability": float("nan")},
                    {"design": ""},
                    {"n_planned": -1},
                    {"preregistration": 4},
                    {"candidate_id": "C-nope"},
                ):
                    payload = dict(base)
                    payload.update(update)
                    with self.assertRaises(ledger_mod.LedgerError):
                        ledger_mod.register(store, **payload)
            finally:
                store.close()

    def test_outcome_validation_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                registered = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="a",
                    primary_outcome="memory",
                )
                experiment_id = registered["experiment"]["experiment_id"]
                for payload in (
                    {"observed_direction": "sideways"},
                    {"observed_direction": "increase", "qc": "bad"},
                    {"observed_direction": "increase", "effect_size": float("nan")},
                    {"observed_direction": "increase", "ci_low": 0.1},
                    {"observed_direction": "increase", "ci_low": 0.2, "ci_high": 0.1},
                    {"observed_direction": "increase", "n": -1},
                    {"observed_direction": "increase", "note": 4},
                ):
                    with self.assertRaises(ledger_mod.LedgerError):
                        ledger_mod.record_outcome(store, experiment_id=experiment_id, **payload)
            finally:
                store.close()

    def test_registration_manifest_power_and_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            sheet = power_mod.calculate("mean", effect_size=0.5, dropout=0.1)
            manifest = {
                "schema": "ntx-registration-v1",
                "hypothesis_id": hypothesis.hypothesis_id,
                "design": "rct",
                "assay": "task",
                "primary_outcome": "memory",
                "prediction": "increase",
                "probability": 0.7,
                "n_planned": 140,
                "power": sheet,
            }
            path = Path(tmp) / "registration.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            try:
                dry = ledger_mod.register_from_file(store, path, dry_run=True)
                self.assertTrue(dry["dry_run"])
                self.assertEqual(dry["power_check"]["required_total"], 140)
                self.assertTrue(dry["power_check"]["meets_required"])
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 0)

                result = ledger_mod.register_from_file(store, path)
                self.assertEqual(result["experiment"]["power"]["power_id"], sheet["power_id"])
                self.assertEqual(result["power_check"]["meets_required"], True)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 1)
                exported = ledger_mod.export_ledger(store)
                self.assertEqual(exported["schema"], "ntx-ledger-v3")
                self.assertEqual(exported["experiments"][0]["power"]["power_id"], sheet["power_id"])

                shortfall = dict(manifest)
                shortfall["assay"] = "second task"
                shortfall["n_planned"] = 10
                path.write_text(json.dumps(shortfall), encoding="utf-8")
                result = ledger_mod.register_from_file(store, path)
                self.assertFalse(result["power_check"]["meets_required"])

                bad = dict(manifest)
                bad["extra"] = True
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.register_from_file(store, path)
                tampered = dict(sheet)
                tampered["n_per_arm"] += 1
                path.write_text(json.dumps(tampered), encoding="utf-8")
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.load_power_file(path)
            finally:
                store.close()

    def test_registration_manifest_cli_and_flag_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            store.close()
            db = str(Path(tmp) / "ledger.db")
            manifest = {
                "schema": "ntx-registration-v1",
                "hypothesis_id": hypothesis.hypothesis_id,
                "design": "rct",
                "assay": "task",
                "primary_outcome": "memory",
                "prediction": "increase",
                "probability": 0.7,
            }
            path = Path(tmp) / "registration.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    ["ledger", "register", "--db", db, "--file", str(path), "--dry-run", "--json"]
                )
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(buffer.getvalue())["dry_run"])
            check = Store(Path(db))
            try:
                self.assertEqual(check.conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 0)
            finally:
                check.close()
            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                code = cli_mod.main(
                    [
                        "ledger",
                        "register",
                        "--db",
                        db,
                        "--file",
                        str(path),
                        "--hypothesis",
                        "H-x",
                    ]
                )
            self.assertEqual(code, 2)
            power_path = Path(tmp) / "power.json"
            sheet = power_mod.calculate("mean", effect_size=0.5)
            power_path.write_text(json.dumps(sheet), encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "ledger",
                        "register",
                        "--db",
                        db,
                        "--hypothesis",
                        hypothesis.hypothesis_id,
                        "--design",
                        "rct",
                        "--assay",
                        "task",
                        "--primary-outcome",
                        "memory",
                        "--prediction",
                        "increase",
                        "--probability",
                        "0.7",
                        "--n-planned",
                        "126",
                        "--power",
                        str(power_path),
                        "--dry-run",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertTrue(payload["power_check"]["provided"])
            self.assertTrue(payload["power_check"]["meets_required"])

    def test_outcome_import_rejects_bad_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                registered = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="a",
                    primary_outcome="memory",
                )
                experiment_id = registered["experiment"]["experiment_id"]
                base = {
                    "experiment_id": experiment_id,
                    "source_id": "bad-1",
                    "observed": "increase",
                }
                path = Path(tmp) / "bad.json"
                cases = []
                missing_source = dict(base)
                missing_source.pop("source_id")
                cases.append(missing_source)
                cases.append(dict(base, source_id=4))
                cases.append(dict(base, observed="sideways"))
                cases.append(dict(base, extra=True))
                cases.append(dict(base, note=4))
                for case in cases:
                    path.write_text(json.dumps([case]), encoding="utf-8")
                    with self.assertRaises(ledger_mod.LedgerError):
                        ledger_mod.import_outcomes(store, path)
                path.write_text("[1]", encoding="utf-8")
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.import_outcomes(store, path)
                path.write_text("", encoding="utf-8")
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.import_outcomes(store, path)
                path.write_text(json.dumps({"schema": "wrong", "outcomes": [base]}), encoding="utf-8")
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.import_outcomes(store, path)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0], 0)
            finally:
                store.close()

    def test_outcome_import_idempotence_and_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                registered = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="a",
                    primary_outcome="memory",
                )
                experiment_id = registered["experiment"]["experiment_id"]
                records = [
                    {
                        "experiment_id": experiment_id,
                        "source_id": "result-1",
                        "observed": "increase",
                        "n": 20,
                        "qc": "pass",
                    },
                    {
                        "experiment_id": experiment_id,
                        "source_id": "result-2",
                        "observed": "decrease",
                        "n": 21,
                        "qc": "warn",
                    },
                ]
                path = Path(tmp) / "results.jsonl"
                path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
                dry = ledger_mod.import_outcomes(store, path, dry_run=True)
                self.assertEqual(dry["would_import"], 2)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0], 0)
                first = ledger_mod.import_outcomes(store, path)
                self.assertEqual(first["imported"], 2)
                second = ledger_mod.import_outcomes(store, path)
                self.assertEqual(second["imported"], 0)
                self.assertEqual(second["skipped"], 2)
                row = store.conn.execute(
                    "SELECT source_id FROM outcomes WHERE source_id='result-1'"
                ).fetchone()
                self.assertEqual(row[0], "result-1")

                conflict = dict(records[0])
                conflict["n"] = 99
                path.write_text(json.dumps([conflict]), encoding="utf-8")
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.import_outcomes(store, path)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0], 2)

                duplicate = [records[0], records[0]]
                path.write_text(json.dumps(duplicate), encoding="utf-8")
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.import_outcomes(store, path)
                unknown = dict(records[0])
                unknown["experiment_id"] = "E-nope"
                path.write_text(json.dumps([unknown]), encoding="utf-8")
                with self.assertRaises(ledger_mod.LedgerError):
                    ledger_mod.import_outcomes(store, path)
                path.write_text(json.dumps({"schema": "ntx-outcomes-v1", "outcomes": []}), encoding="utf-8")
                empty = ledger_mod.import_outcomes(store, path)
                self.assertEqual(empty["imported"], 0)
            finally:
                store.close()

    def test_outcome_import_cli_and_manual_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                registered = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="a",
                    primary_outcome="memory",
                )
                experiment_id = registered["experiment"]["experiment_id"]
                manual = ledger_mod.record_outcome(
                    store,
                    experiment_id=experiment_id,
                    observed_direction="increase",
                    source_id="manual-1",
                )
                self.assertEqual(manual["outcome"]["source_id"], "manual-1")
                path = Path(tmp) / "results.json"
                path.write_text(
                    json.dumps(
                        [
                            {
                                "experiment_id": experiment_id,
                                "source_id": "import-1",
                                "observed": "decrease",
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = cli_mod.main(
                        [
                            "ledger",
                            "import-outcomes",
                            "--db",
                            str(Path(tmp) / "ledger.db"),
                            "--file",
                            str(path),
                            "--json",
                        ]
                    )
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(buffer.getvalue())["imported"], 1)
            finally:
                store.close()

    def test_group_metrics_and_human_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                for probability in (0.6, 0.7, 0.8):
                    result = ledger_mod.register(
                        store,
                        hypothesis_id=hypothesis.hypothesis_id,
                        prediction="increase",
                        probability=probability,
                        design="rct",
                        assay="a",
                        primary_outcome="memory",
                    )
                    ledger_mod.record_outcome(
                        store,
                        experiment_id=result["experiment"]["experiment_id"],
                        observed_direction="increase",
                        n=10,
                    )
                payload = ledger_mod.score(store)
                self.assertAlmostEqual(payload["by_kind"]["contradiction"]["brier"], 0.0967, places=4)
                self.assertEqual(payload["by_design"]["rct"]["n"], 3)
                self.assertIn("Directional prediction score", ledger_mod.format_score(payload))
                self.assertIn("Prediction ledger", ledger_mod.format_status(ledger_mod.status(store)))
                empty = Store(Path(tmp) / "empty.db")
                try:
                    self.assertIn(
                        "no eligible observations", ledger_mod.format_score(ledger_mod.score(empty))
                    )
                finally:
                    empty.close()
            finally:
                store.close()

    def test_export_file_and_cli_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = self._store(tmp)
            try:
                ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="a",
                    primary_outcome="memory",
                )
                out = Path(tmp) / "nested" / "ledger.json"
                payload = ledger_mod.write_export(store, out)
                self.assertTrue(out.is_file())
                self.assertEqual(
                    json.loads(out.read_text(encoding="utf-8"))["content_hash"],
                    payload["content_hash"],
                )
            finally:
                store.close()

            db = str(Path(tmp) / "ledger.db")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["ledger", "export", "--db", db, "--out", str(Path(tmp) / "cli.json")])
            self.assertEqual(code, 0)
            self.assertIn("content hash", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["ledger", "score", "--db", db])
            self.assertEqual(code, 0)
            self.assertIn("no eligible observations", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["ledger", "status", "--db", db])
            self.assertEqual(code, 0)
            self.assertIn("Prediction ledger", buffer.getvalue())


class TestPowerWorksheet(unittest.TestCase):
    def test_mean_and_proportion_reference_values(self):
        mean = power_mod.calculate("mean", effect_size=0.5, alpha=0.05, target_power=0.8, dropout=0.1)
        self.assertEqual(mean["n_per_arm"], 63)
        self.assertEqual(mean["n_total"], 126)
        self.assertEqual(mean["n_per_arm_with_dropout"], 70)
        self.assertEqual(mean["n_total_with_dropout"], 140)
        self.assertEqual(
            mean["power_id"],
            power_mod.calculate("mean", effect_size=0.5, alpha=0.05, target_power=0.8, dropout=0.1)[
                "power_id"
            ],
        )
        self.assertEqual(power_mod.validate_power(mean), mean)

        proportion = power_mod.calculate(
            "proportion", p1=0.2, p2=0.4, alpha=0.05, target_power=0.8, dropout=0.1
        )
        self.assertEqual(proportion["n_per_arm"], 82)
        self.assertEqual(proportion["n_total"], 164)
        self.assertEqual(proportion["n_per_arm_with_dropout"], 92)
        self.assertEqual(proportion["n_total_with_dropout"], 184)
        self.assertAlmostEqual(proportion["effect_size"], 0.2)

        one_sided = power_mod.calculate("mean", effect_size=0.5, sided="one")
        self.assertEqual(one_sided["n_per_arm"], 50)
        self.assertEqual(one_sided["n_total"], 100)

    def test_power_validation_rejects_bad_inputs_and_tampering(self):
        for kwargs in (
            {"test": "odds", "effect_size": 0.5},
            {"test": "mean", "effect_size": 0},
            {"test": "mean", "effect_size": float("nan")},
            {"test": "mean", "effect_size": 0.5, "alpha": 0.6},
            {"test": "mean", "effect_size": 0.5, "target_power": 0.4},
            {"test": "mean", "effect_size": 0.5, "dropout": 1.0},
            {"test": "mean", "effect_size": 0.5, "sided": "both"},
            {"test": "mean", "effect_size": 0.5, "p1": 0.2},
            {"test": "proportion", "p1": 0.2, "p2": 0.2},
            {"test": "proportion", "p1": 0, "p2": 0.2},
            {"test": "proportion", "p1": 0.2, "p2": 0.4, "effect_size": 0.2},
            {"test": "mean", "effect_size": 10**1000},
            {"test": "mean", "effect_size": float("inf")},
            {"test": "mean", "effect_size": 1e-320},
        ):
            with self.assertRaises(power_mod.PowerError):
                power_mod.calculate(**kwargs)
        for bad in (None, [], {}, {"schema": "wrong"}):
            with self.assertRaises(power_mod.PowerError):
                power_mod.validate_power(bad)
        sheet = power_mod.calculate("mean", effect_size=0.5)
        tampered = dict(sheet)
        tampered["n_per_arm"] += 1
        with self.assertRaises(power_mod.PowerError):
            power_mod.validate_power(tampered)
        method = dict(sheet)
        method["method"] = "something else"
        with self.assertRaises(power_mod.PowerError):
            power_mod.validate_power(method)
        unknown = dict(sheet)
        unknown["extra"] = 1
        with self.assertRaises(power_mod.PowerError):
            power_mod.validate_power(unknown)
        self.assertIn("Power worksheet", power_mod.format_power(sheet))

    def test_power_cli_writes_and_prints(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "power.json"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "power",
                        "--test",
                        "mean",
                        "--effect-size",
                        "0.5",
                        "--dropout",
                        "0.1",
                        "--out",
                        str(out),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("wrote", buffer.getvalue())
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["n_total_with_dropout"], 140)

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["power", "--test", "proportion", "--p1", "0.2", "--p2", "0.4", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buffer.getvalue())["n_per_arm"], 82)

            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                code = cli_mod.main(["power", "--test", "mean", "--effect-size", "0"])
            self.assertEqual(code, 2)


class TestCalibration(unittest.TestCase):
    def test_reference_table_and_decomposition(self):
        rows = [
            {"probability": 0.6, "agree": 1, "cluster": "h1"},
            {"probability": 0.6, "agree": 0, "cluster": "h2"},
            {"probability": 0.8, "agree": 1, "cluster": "h3"},
            {"probability": 0.8, "agree": 1, "cluster": "h4"},
        ]
        payload = calibration_mod.analyze(rows)
        self.assertEqual(payload["schema"], "ntx-calibration-v2")
        self.assertEqual(payload["n"], 4)
        self.assertEqual(payload["n_clusters"], 4)
        self.assertEqual(payload["brier"], 0.15)
        self.assertEqual(payload["brier_skill"], 0.4)
        self.assertEqual(payload["accuracy"], 0.75)
        self.assertEqual(payload["ece"], 0.15)
        self.assertEqual(payload["mce"], 0.2)
        self.assertEqual(
            payload["decomposition"],
            {"reliability": 0.025, "resolution": 0.0625, "uncertainty": 0.1875},
        )
        self.assertTrue(payload["underpowered"])
        self.assertTrue(payload["descriptive_only"])
        self.assertEqual(payload["sparse_bins"], ["0.60-0.70", "0.80-0.90"])
        self.assertEqual(payload["bootstrap"], None)
        self.assertIn("Calibration (descriptive)", calibration_mod.format_calibration(payload))
        self.assertIn("underpowered", calibration_mod.format_calibration(payload))

    def test_empty_inputs_boundaries_and_validation(self):
        empty = calibration_mod.analyze([], pending=2, excluded=1)
        self.assertEqual(empty["n"], 0)
        self.assertEqual(empty["pending"], 2)
        self.assertEqual(empty["excluded"], 1)
        self.assertIsNone(empty["brier"])
        self.assertIn("no eligible observations", calibration_mod.format_calibration(empty))

        boundaries = calibration_mod.analyze(
            [
                {"probability": 0.5, "agree": 1, "cluster": "h1"},
                {"probability": 0.6, "agree": 0, "cluster": "h2"},
                {"probability": 0.999, "agree": 1, "cluster": "h3"},
            ]
        )
        self.assertEqual(boundaries["table"][0]["n"], 1)
        self.assertEqual(boundaries["table"][1]["n"], 1)
        self.assertEqual(boundaries["table"][-1]["n"], 1)

        for kwargs in (
            {"rows": [], "bins": 1},
            {"rows": [], "bins": 11},
            {"rows": [], "n_boot": -1},
            {"rows": [], "n_boot": 6000},
            {"rows": [{"probability": 0.49, "agree": 1, "cluster": "h1"}]},
            {"rows": [{"probability": 1.0, "agree": 1, "cluster": "h1"}]},
            {"rows": [{"probability": 0.6, "agree": 2, "cluster": "h1"}]},
            {"rows": [{"probability": 0.6, "agree": True, "cluster": "h1"}]},
            {"rows": [{"probability": 0.6, "agree": 1}]},
            {"rows": [], "cluster": "paper"},
        ):
            with self.assertRaises(calibration_mod.CalibrationError):
                calibration_mod.analyze(**kwargs)

    def test_bootstrap_is_deterministic(self):
        rows = [
            {
                "probability": 0.6 if index % 2 else 0.8,
                "agree": 1 if index % 3 else 0,
                "cluster": "h%d" % index,
            }
            for index in range(12)
        ]
        first = calibration_mod.analyze(rows, n_boot=100)
        second = calibration_mod.analyze(rows, n_boot=100)
        self.assertEqual(first["bootstrap"], second["bootstrap"])
        self.assertGreaterEqual(first["bootstrap"]["brier_ci"][0], 0.0)
        self.assertLessEqual(first["bootstrap"]["brier_ci"][0], first["brier"])
        self.assertLessEqual(first["brier"], first["bootstrap"]["brier_ci"][1])
        self.assertEqual(first["bootstrap"]["n_boot"], 100)
        self.assertFalse(first["underpowered"])

    def test_cluster_bootstrap_width_and_threshold(self):
        rows = [
            {
                "probability": 0.8,
                "agree": 1 if cluster < 10 else 0,
                "cluster": "h%d" % cluster,
                "kind": "k",
                "design": "d",
            }
            for cluster in range(20)
            for _ in range(5)
        ]
        grouped = calibration_mod.analyze(rows, n_boot=200)
        unique = calibration_mod.analyze(
            [{**row, "cluster": "e%d" % index} for index, row in enumerate(rows)], n_boot=200
        )
        grouped_width = grouped["bootstrap"]["brier_ci"][1] - grouped["bootstrap"]["brier_ci"][0]
        unique_width = unique["bootstrap"]["brier_ci"][1] - unique["bootstrap"]["brier_ci"][0]
        self.assertEqual(grouped["n_clusters"], 20)
        self.assertEqual(unique["n_clusters"], 100)
        self.assertGreater(grouped_width, unique_width * 2)

        same_cluster = calibration_mod.analyze(
            [{"probability": 0.7, "agree": 1, "cluster": "h1"} for _ in range(12)]
        )
        self.assertEqual(same_cluster["underpowered_reasons"], ["clusters<10"])
        self.assertIsNone(same_cluster["bootstrap"])

    def test_sensitivity_precision_missingness_and_corrections(self):
        rows = [
            {
                "probability": 0.6,
                "agree": 1,
                "cluster": "h1",
                "observed": "increase",
                "ci_low": 0.1,
                "ci_high": 0.5,
            },
            {
                "probability": 0.6,
                "agree": 0,
                "cluster": "h2",
                "observed": "decrease",
                "ci_low": -0.5,
                "ci_high": 0.1,
            },
            {
                "probability": 0.8,
                "agree": 1,
                "cluster": "h3",
                "observed": "increase",
                "ci_low": -0.5,
                "ci_high": -0.1,
            },
            {"probability": 0.8, "agree": 1, "cluster": "h4", "observed": "increase"},
        ]
        variants = {
            "first_outcome": rows[:2],
            "qc_pass_only": rows[:3],
            "precise_ci_only": rows[:1],
        }
        payload = calibration_mod.analyze(
            rows,
            sensitivity=variants,
            missingness=[
                {
                    "design": "rct",
                    "registered": 6,
                    "eligible": 4,
                    "pending": 1,
                    "excluded": 1,
                    "eligible_fraction": 0.6667,
                }
            ],
            corrections={
                "outcomes_total": 5,
                "experiments_with_multiple_outcomes": 1,
                "experiments_eligible": 4,
            },
        )
        self.assertEqual(
            payload["precision"],
            {"ci_consistent": 1, "ci_imprecise": 1, "ci_inconsistent": 1, "ci_missing": 1},
        )
        self.assertEqual(payload["sensitivity"]["first_outcome"]["n"], 2)
        self.assertIsNotNone(payload["sensitivity"]["first_outcome"]["delta_brier"])
        self.assertEqual(payload["sensitivity"]["precise_ci_only"]["n"], 1)
        self.assertEqual(payload["corrections"]["outcomes_total"], 5)
        self.assertEqual(payload["missingness"][0]["design"], "rct")
        self.assertIn("inconsistent CI", payload["warning"])

    def test_html_hash_and_empty_report(self):
        rows = [
            {
                "probability": 0.7,
                "agree": 1,
                "cluster": "h1",
                "design": "<script>alert(1)</script>",
            }
        ]
        first = calibration_mod.analyze(rows)
        first["generated_at"] = "2026-01-01T00:00:00+00:00"
        second = calibration_mod.analyze(rows)
        second["generated_at"] = "2026-01-02T00:00:00+00:00"
        self.assertEqual(calibration_mod.content_hash(first), calibration_mod.content_hash(second))
        html = calibration_html.render_html(first)
        self.assertIn("<svg", html)
        self.assertIn("Reliability", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        empty_html = calibration_html.render_html(calibration_mod.analyze([]))
        self.assertIn("no eligible observations", empty_html)

        boot_rows = [
            {"probability": 0.7, "agree": index % 2, "cluster": "h%d" % index} for index in range(12)
        ]
        boot_html = calibration_html.render_html(calibration_mod.analyze(boot_rows, n_boot=50))
        self.assertIn("Brier 95% CI (50 resamples)", boot_html)

    def test_cli_calibration_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = TestPredictionLedger()._store(tmp)
            try:
                for index, (probability, observed) in enumerate(
                    ((0.6, "increase"), (0.6, "decrease"), (0.8, "increase"), (0.8, "increase"))
                ):
                    registered = ledger_mod.register(
                        store,
                        hypothesis_id=hypothesis.hypothesis_id,
                        prediction="increase",
                        probability=probability,
                        design="rct",
                        assay="calibration-%d" % index,
                        primary_outcome="memory",
                    )
                    ledger_mod.record_outcome(
                        store,
                        experiment_id=registered["experiment"]["experiment_id"],
                        observed_direction=observed,
                    )
                ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="calibration-pending",
                    primary_outcome="memory",
                )
                failed = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="calibration-failed",
                    primary_outcome="memory",
                )
                ledger_mod.record_outcome(
                    store,
                    experiment_id=failed["experiment"]["experiment_id"],
                    observed_direction="increase",
                    qc="fail",
                )
                payload = ledger_mod.calibration(store)
                self.assertEqual(payload["schema"], "ntx-calibration-v2")
                self.assertEqual(payload["n"], 4)
                self.assertEqual(payload["n_clusters"], 1)
                self.assertEqual(payload["pending"], 1)
                self.assertEqual(payload["excluded"], 1)
                self.assertEqual(payload["brier"], 0.15)
                self.assertIsNone(payload["bootstrap"])
                self.assertEqual(payload["corrections"]["outcomes_total"], 5)
                self.assertEqual(payload["missingness"][0]["registered"], 6)
                db = str(Path(tmp) / "ledger.db")
            finally:
                store.close()

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["ledger", "calibration", "--db", db])
            self.assertEqual(code, 0)
            self.assertIn("Calibration (descriptive)", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["ledger", "calibration", "--db", db, "--json", "--bins", "3"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buffer.getvalue())["bins"], 3)

            json_out = Path(tmp) / "report.json"
            html_out = Path(tmp) / "report.html"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "ledger",
                        "calibration",
                        "--db",
                        db,
                        "--cluster",
                        "experiment",
                        "--out",
                        str(json_out),
                        "--html",
                        str(html_out),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            report = json.loads(json_out.read_text(encoding="utf-8"))
            self.assertEqual(report["n_clusters"], 4)
            self.assertEqual(report["cluster"], "experiment")
            self.assertEqual(report["content_hash"], calibration_mod.content_hash(report))
            self.assertIn("<svg", html_out.read_text(encoding="utf-8"))

            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                code = cli_mod.main(["ledger", "calibration", "--db", db, "--bins", "1"])
            self.assertEqual(code, 2)


class TestSourceVerification(unittest.TestCase):
    def _store(self, tmp: str) -> Store:
        store = Store(Path(tmp) / "verify.db")
        store.add_document(
            Document(
                doc_id="DOI:one",
                doi="10.1234/one",
                title="Oxytocin <i>&amp;</i> memory",
                year=2020,
            )
        )
        store.add_document(
            Document(
                doc_id="PMID:2",
                pmid="2",
                title="Retracted result",
                year=2021,
            )
        )
        store.add_document(
            Document(
                doc_id="DOI:three",
                doi="10.1234/three",
                title="Local title",
                year=2019,
            )
        )
        store.add_document(
            Document(
                doc_id="DOI:five",
                doi="10.1234/five",
                title="Oxytocin <i>&amp;</i> memory",
                year=2020,
                retraction_status="retracted",
            )
        )
        store.add_document(Document(doc_id="LOCAL:4", title="No identifier"))
        return store

    @staticmethod
    def _fake(url: str, cache_dir: Path, offline: bool = False, **kwargs: Any) -> dict[str, Any]:
        if "crossref" in url:
            if "three" in url:
                return {
                    "message": {
                        "title": ["Different title"],
                        "container-title": ["Journal"],
                        "issued": {"date-parts": [[2018]]},
                    }
                }
            return {
                "message": {
                    "title": ["<i>Oxytocin</i> &amp; memory"],
                    "container-title": ["Journal"],
                    "issued": {"date-parts": [[2020]]},
                }
            }
        return {
            "result": {
                "2": {
                    "title": "Retracted result",
                    "fulljournalname": "Journal",
                    "pubdate": "2021",
                    "pubtype": ["Retracted Publication"],
                }
            }
        }

    def test_verified_mismatch_retraction_and_unverifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                with mock.patch.object(verify_mod.http, "get_json", side_effect=self._fake):
                    payload = verify_mod.verify_documents(store, cache_dir=Path(tmp))
                by_id = {item["doc_id"]: item for item in payload["items"]}
                self.assertEqual(by_id["DOI:one"]["status"], "verified")
                self.assertTrue(by_id["DOI:one"]["title_match"])
                self.assertEqual(by_id["PMID:2"]["status"], "retraction_drift")
                self.assertEqual(by_id["PMID:2"]["remote_status"], "retracted")
                self.assertEqual(by_id["DOI:three"]["status"], "metadata_mismatch")
                self.assertFalse(by_id["DOI:three"]["title_match"])
                self.assertFalse(by_id["DOI:three"]["year_match"])
                self.assertEqual(by_id["DOI:five"]["status"], "local_retraction")
                self.assertEqual(by_id["LOCAL:4"]["status"], "unverifiable")
                self.assertEqual(payload["counts"]["verified"], 1)
                self.assertEqual(payload["counts"]["retraction_drift"], 1)
                self.assertEqual(payload["counts"]["local_retraction"], 1)
                self.assertIn("Source verification", verify_mod.format_verification(payload))
            finally:
                store.close()

    def test_offline_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            try:
                with mock.patch.object(
                    verify_mod.http,
                    "get_json",
                    side_effect=http_mod.OfflineError("cache miss"),
                ):
                    payload = verify_mod.verify_documents(
                        store, doc_id="DOI:one", cache_dir=Path(tmp), offline=True
                    )
                self.assertEqual(payload["items"][0]["status"], "unavailable")
                self.assertIn("offline cache miss", payload["items"][0]["detail"])
                with mock.patch.object(verify_mod.http, "get_json", side_effect=self._fake) as fetch:
                    limited = verify_mod.verify_documents(store, limit=1, cache_dir=Path(tmp))
                self.assertEqual(limited["checked"], 1)
                self.assertFalse(fetch.call_args.args[2])
            finally:
                store.close()

    def test_cli_verify_json_out_and_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            db = str(Path(tmp) / "verify.db")
            store.close()
            out = Path(tmp) / "verification.json"
            with mock.patch.object(verify_mod.http, "get_json", side_effect=self._fake):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = cli_mod.main(
                        [
                            "verify",
                            "--db",
                            db,
                            "--cache",
                            tmp,
                            "--out",
                            str(out),
                            "--json",
                        ]
                    )
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["schema"], "ntx-source-verification-v1")
            self.assertEqual(payload["content_hash"], verify_mod._content_hash(payload))
            self.assertTrue(out.exists())
            with (
                mock.patch.object(verify_mod.http, "get_json", side_effect=self._fake),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = cli_mod.main(["verify", "--db", db, "--cache", tmp, "--fail-on-drift"])
            self.assertEqual(code, 1)
            with (
                mock.patch.object(
                    verify_mod.http,
                    "get_json",
                    side_effect=http_mod.OfflineError("cache miss"),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = cli_mod.main(
                    [
                        "verify",
                        "--db",
                        db,
                        "--doc-id",
                        "DOI:one",
                        "--cache",
                        tmp,
                        "--offline",
                        "--fail-on-drift",
                    ]
                )
            self.assertEqual(code, 0)
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli_mod.main(["verify", "--db", db, "--doc-id", "missing"])
            self.assertEqual(code, 2)


class TestLedgerCheckpoints(unittest.TestCase):
    def test_chain_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = TestPredictionLedger()._store(tmp)
            try:
                first = ledger_mod.build_checkpoint(store)
                self.assertEqual(first["sequence"], 1)
                self.assertIsNone(first["previous_hash"])
                first_path = Path(tmp) / "0001.json"
                ledger_mod.write_checkpoint(store, first_path)
                second = ledger_mod.write_checkpoint(store, Path(tmp) / "0002.json", previous_path=first_path)
                report = ledger_mod.verify_checkpoint_chain([first_path, Path(tmp) / "0002.json"], store)
                self.assertTrue(report["valid"])
                self.assertEqual(report["count"], 2)
                self.assertEqual(second["previous_hash"], first["content_hash"])

                tampered = dict(second)
                tampered["ledger_hash"] = "0" * 64
                self.assertIn("content_hash_mismatch", ledger_mod.checkpoint_issues(tampered))

                bad_path = Path(tmp) / "bad.json"
                bad_path.write_text(json.dumps(tampered), encoding="utf-8")
                bad_report = ledger_mod.verify_checkpoint_chain([first_path, bad_path], store)
                self.assertFalse(bad_report["valid"])
                self.assertTrue(any("content_hash_mismatch" in issue for issue in bad_report["issues"]))
            finally:
                store.close()

    def test_sequence_gap_and_current_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, hypothesis = TestPredictionLedger()._store(tmp)
            try:
                first = ledger_mod.build_checkpoint(store)
                path = Path(tmp) / "0001.json"
                path.write_text(json.dumps(first), encoding="utf-8")
                gap = dict(first)
                gap["sequence"] = 3
                gap["content_hash"] = ledger_mod._checkpoint_hash(gap)
                gap_path = Path(tmp) / "0003.json"
                gap_path.write_text(json.dumps(gap), encoding="utf-8")
                report = ledger_mod.verify_checkpoint_chain([path, gap_path], store)
                self.assertFalse(report["valid"])
                self.assertTrue(any("sequence_gap_or_duplicate" in issue for issue in report["issues"]))

                registered = ledger_mod.register(
                    store,
                    hypothesis_id=hypothesis.hypothesis_id,
                    prediction="increase",
                    probability=0.7,
                    design="rct",
                    assay="checkpoint-smoke",
                    primary_outcome="memory",
                )
                ledger_mod.record_outcome(
                    store,
                    experiment_id=registered["experiment"]["experiment_id"],
                    observed_direction="increase",
                )
                changed = ledger_mod.verify_checkpoint_chain([path], store)
                self.assertFalse(changed["valid"])
                self.assertIn("ledger_hash_mismatch", changed["issues"])
            finally:
                store.close()

    def test_cli_checkpoint_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = TestPredictionLedger()._store(tmp)
            db = str(Path(tmp) / "ledger.db")
            store.close()
            directory = Path(tmp) / "checkpoints"
            first = directory / "0001.json"
            second = directory / "0002.json"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["ledger", "checkpoint", "--db", db, "--out", str(first)])
            self.assertEqual(code, 0)
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli_mod.main(
                    [
                        "ledger",
                        "checkpoint",
                        "--db",
                        db,
                        "--out",
                        str(second),
                        "--previous",
                        str(first),
                    ]
                )
            self.assertEqual(code, 0)
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli_mod.main(["ledger", "verify-checkpoints", "--db", db, "--dir", str(directory)])
            self.assertEqual(code, 0)
            with contextlib.redirect_stderr(io.StringIO()):
                code = cli_mod.main(["ledger", "checkpoint", "--db", db, "--out", str(first)])
            self.assertEqual(code, 2)
            tampered = json.loads(second.read_text(encoding="utf-8"))
            tampered["ledger_hash"] = "f" * 64
            tampered["content_hash"] = ledger_mod._checkpoint_hash(tampered)
            second.write_text(json.dumps(tampered), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli_mod.main(["ledger", "verify-checkpoints", "--db", db, "--dir", str(directory)])
            self.assertEqual(code, 1)


class TestPrecursorGeneration(unittest.TestCase):
    def _precursor_store(self, tmp: str) -> tuple[Store, str]:
        path = Path(tmp) / "precursor.jsonl"
        path.write_text(
            json.dumps(
                {
                    "name": "test precursor",
                    "sequence": "AKRPLG",
                    "source": "unit-test",
                    "uniprot": "TEST1",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        store = Store(Path(tmp) / "generation.db")
        result = precursors_mod.import_precursors(store, path)
        return store, result["precursors"][0]["precursor_id"]

    def test_precursor_import_validation_and_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, precursor_id = self._precursor_store(tmp)
            try:
                self.assertTrue(precursor_id.startswith("PR-"))
                row = precursors_mod.get_precursor(store, precursor_id)
                self.assertEqual(row["sequence"], "AKRPLG")
                self.assertEqual(row["uniprot"], "TEST1")
                path = Path(tmp) / "precursor.jsonl"
                with self.assertRaises(precursors_mod.PrecursorError):
                    precursors_mod.import_precursors(store, path)
                self.assertIsNone(precursors_mod.get_precursor(store, "PR-nope"))
            finally:
                store.close()

            bad = Path(tmp) / "bad.jsonl"
            bad.write_text(
                json.dumps({"name": "bad", "sequence": "AC1D"})
                + "\n"
                + json.dumps({"name": "bad2", "sequence": "A"})
                + "\n",
                encoding="utf-8",
            )
            store = Store(Path(tmp) / "bad.db")
            try:
                with self.assertRaises(precursors_mod.PrecursorError) as ctx:
                    precursors_mod.import_precursors(store, bad)
                self.assertIn("line 1", str(ctx.exception))
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM precursors").fetchone()[0], 0)
            finally:
                store.close()

    def test_precursor_dry_run_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bom.jsonl"
            path.write_text(
                "\ufeff" + json.dumps({"name": "bom", "sequence": "ACDE"}) + "\n",
                encoding="utf-8",
            )
            store = Store(Path(tmp) / "bom.db")
            try:
                result = precursors_mod.import_precursors(store, path, dry_run=True)
                self.assertEqual(result["imported"], 1)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM precursors").fetchone()[0], 0)
                precursors_mod.import_precursors(store, path)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM precursors").fetchone()[0], 1)
            finally:
                store.close()

    def test_cleavage_rules_respect_proline_and_termini(self):
        self.assertEqual(generate_mod.cleavage_positions("AKRPLG", "trypsin"), [2])
        self.assertEqual(generate_mod.cleavage_positions("AAYF", "chymotrypsin"), [3])
        self.assertEqual(generate_mod.cleavage_positions("AKRPK", "trypsin"), [2])
        with self.assertRaises(generate_mod.GenerationError):
            generate_mod.cleavage_positions("AK", "unknown")

    def test_cleavage_generates_missed_cleavages_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, precursor_id = self._precursor_store(tmp)
            try:
                first = generate_mod.generate_cleavage(
                    store, precursor_id, enzyme="trypsin", missed_cleavages=1
                )
                self.assertEqual(first["considered"], 3)
                self.assertEqual(first["generated"], 3)
                self.assertEqual({item["sequence"] for item in first["candidates"]}, {"AK", "RPLG", "AKRPLG"})
                second = generate_mod.generate_cleavage(store, precursor_id, missed_cleavages=1)
                self.assertEqual(second["generated"], 0)
                self.assertEqual(second["existing"], 3)
                narrow = generate_mod.generate_cleavage(store, precursor_id, missed_cleavages=0, min_length=4)
                self.assertEqual({item["sequence"] for item in narrow["candidates"]}, {"RPLG"})
            finally:
                store.close()

    def test_cleavage_dry_run_cap_and_unknown_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, precursor_id = self._precursor_store(tmp)
            try:
                dry = generate_mod.generate_cleavage(store, precursor_id, missed_cleavages=1, dry_run=True)
                self.assertEqual(dry["generated"], 3)
                self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)
                with self.assertRaises(generate_mod.GenerationError):
                    generate_mod.generate_cleavage(store, precursor_id, max_candidates=1)
                with self.assertRaises(generate_mod.GenerationError):
                    generate_mod.generate_cleavage(store, "PR-nope")
                with self.assertRaises(generate_mod.GenerationError):
                    generate_mod.generate_cleavage(store, precursor_id, missed_cleavages=4)
                with self.assertRaises(generate_mod.GenerationError):
                    generate_mod.generate_cleavage(store, precursor_id, min_length=20, max_length=10)
            finally:
                store.close()

    def test_analogs_have_explicit_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, precursor_id = self._precursor_store(tmp)
            try:
                generated = generate_mod.generate_cleavage(store, precursor_id, missed_cleavages=1)
                parent = next(item for item in generated["candidates"] if item["sequence"] == "RPLG")
                result = generate_mod.generate_analogs(store, parent["candidate_id"], truncations=2)
                self.assertEqual(result["considered"], 8)
                self.assertEqual(result["generated"], 8)
                self.assertEqual(
                    {item["sequence"] for item in result["candidates"]},
                    {"APLG", "LG", "PLG", "RALG", "RP", "RPAG", "RPL", "RPLA"},
                )
                self.assertTrue(all(not item["modifications"] for item in result["candidates"]))
                child = result["candidates"][0]
                links = generate_mod.lineage_rows(store, child["candidate_id"])
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0]["source_id"], parent["candidate_id"])
                self.assertEqual(links[0]["source_kind"], "candidate")
                second = generate_mod.generate_analogs(store, parent["candidate_id"], truncations=2)
                self.assertEqual(second["generated"], 0)
                self.assertEqual(second["existing"], 8)
            finally:
                store.close()

    def test_analog_cap_and_operation_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, precursor_id = self._precursor_store(tmp)
            try:
                generated = generate_mod.generate_cleavage(store, precursor_id, missed_cleavages=1)
                parent = next(item for item in generated["candidates"] if item["sequence"] == "RPLG")
                with self.assertRaises(generate_mod.GenerationError):
                    generate_mod.generate_analogs(store, parent["candidate_id"], max_variants=1)
                with self.assertRaises(generate_mod.GenerationError):
                    generate_mod.generate_analogs(
                        store, parent["candidate_id"], truncations=0, alanine_scan=False
                    )
                with self.assertRaises(generate_mod.GenerationError):
                    generate_mod.generate_analogs(store, "C-nope")
            finally:
                store.close()

    def test_schema_v5_tables_are_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.db"
            store = Store(path)
            try:
                self.assertEqual(store.schema_version, SCHEMA_VERSION)
                store.conn.executescript(
                    "DROP TABLE candidate_lineage; DROP TABLE precursors; PRAGMA user_version=4;"
                )
                store.conn.commit()
            finally:
                store.close()
            upgraded = Store(path)
            try:
                self.assertEqual(upgraded.schema_version, SCHEMA_VERSION)
                tables = {
                    row[0]
                    for row in upgraded.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertIn("precursors", tables)
                self.assertIn("candidate_lineage", tables)
                upgraded.add_precursor(
                    Precursor(
                        precursor_id="PR-test",
                        name="x",
                        sequence="ACDE",
                        length=4,
                        created_at="2026-01-01T00:00:00+00:00",
                    )
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    upgraded.conn.execute("UPDATE precursors SET name='y' WHERE precursor_id='PR-test'")
                upgraded.conn.rollback()
            finally:
                upgraded.close()

    def test_export_includes_precursors_and_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, precursor_id = self._precursor_store(tmp)
            try:
                generate_mod.generate_cleavage(store, precursor_id, missed_cleavages=1)
                payload = ledger_mod.export_ledger(store)
                self.assertEqual(payload["schema"], "ntx-ledger-v3")
                self.assertEqual(len(payload["precursors"]), 1)
                self.assertEqual(len(payload["lineage"]), 3)
                self.assertEqual(payload["content_hash"], ledger_mod.export_ledger(store)["content_hash"])
            finally:
                store.close()

    def test_precursor_validation_errors_and_formatting(self):
        self.assertEqual(precursors_mod.validate_precursor_sequence(" acde "), "ACDE")
        for value in (5, "", "AC1D", "A"):
            with self.assertRaises(precursors_mod.PrecursorError):
                precursors_mod.validate_precursor_sequence(value)
        self.assertEqual(precursors_mod.format_precursors([]), "no precursors in this database")
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "errors.db")
            try:
                cases = [
                    {},
                    [{"name": "x", "sequence": "ACDE", "source": 1}],
                    [{"name": "x", "sequence": "ACDE", "uniprot": 1}],
                    [{"name": "x", "sequence": "ACDE", "notes": 1}],
                    [{"sequence": "ACDE"}],
                ]
                for index, payload in enumerate(cases):
                    path = Path(tmp) / ("case%d.json" % index)
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(precursors_mod.PrecursorError):
                        precursors_mod.import_precursors(store, path)
                with self.assertRaises(precursors_mod.PrecursorError):
                    precursors_mod.import_precursors(store, Path(tmp) / "missing.json")
            finally:
                store.close()

    def test_generation_cli_json_and_human_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "cli.db")
            path = Path(tmp) / "pre.jsonl"
            path.write_text(
                json.dumps({"name": "cli precursor", "sequence": "AKRPLG"}) + "\n", encoding="utf-8"
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["precursor", "import", "--db", db, "--file", str(path), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())
            precursor_id = payload["precursors"][0]["precursor_id"]

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(
                    [
                        "generate",
                        "cleavage",
                        "--db",
                        db,
                        "--precursor",
                        precursor_id,
                        "--missed",
                        "1",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            generated = json.loads(buffer.getvalue())
            self.assertEqual(generated["generated"], 3)
            candidate_id = generated["candidates"][0]["candidate_id"]

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["candidate", "lineage", "--db", db, "--id", candidate_id])
            self.assertEqual(code, 0)
            self.assertIn("lineage for", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["precursor", "list", "--db", db])
            self.assertEqual(code, 0)
            self.assertIn("cli precursor", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli_mod.main(["generate", "analogs", "--db", db, "--candidate", candidate_id])
            self.assertEqual(code, 0)
            self.assertIn("analogs: parent=", buffer.getvalue())

            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                code = cli_mod.main(["precursor", "show", "--db", db, "--id", "PR-nope"])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
