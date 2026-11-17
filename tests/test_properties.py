"""Fixed-seed property tests for exact spans and text-free annotations."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ntx import annotation  # noqa: E402


def _record(text: str, start: int, end: int) -> dict:
    return {
        "doc_id": "PMID:PROPERTY",
        "schema_version": annotation.SCHEMA_VERSION,
        "mentions": [
            {
                "entity_id": "NP:OXT",
                "surface": text[start:end],
                "start": start,
                "end": end,
                "mention_form": "peptide",
            }
        ],
        "claims": [
            {
                "entity_id": "NP:OXT",
                "direction": "increase",
                "outcome": "memory",
                "role": "intervention",
                "sentence": text[start:end],
                "start": start,
                "end": end,
            }
        ],
    }


class TestAnnotationProperties(unittest.TestCase):
    def test_repeated_redact_hydrate_round_trip(self):
        rng = random.Random(20261117)
        alphabet = "abcdefghijklmnopqrstuvwxyz 0123456789.,;:-"
        for _ in range(80):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(20, 160)))
            start = rng.randint(0, len(text) - 2)
            end = rng.randint(start + 1, len(text))
            original = _record(text, start, end)
            redacted = annotation.redact_record(original)
            self.assertTrue(annotation.is_redacted(redacted))
            self.assertEqual(original["claims"][0]["sentence"], text[start:end])
            self.assertNotIn("sentence", redacted["claims"][0])
            self.assertNotIn("surface", redacted["mentions"][0])
            hydrated = annotation.hydrate_record(redacted, text)
            self.assertEqual(hydrated["claims"][0]["sentence"], text[start:end])
            self.assertEqual(hydrated["mentions"][0]["surface"], text[start:end])
            self.assertEqual(annotation.validate_record(hydrated, text), [])

    def test_tampered_claim_hash_always_fails(self):
        rng = random.Random(7)
        for _ in range(40):
            text = "x" * rng.randint(10, 80)
            redacted = annotation.redact_record(_record(text, 1, 5))
            digest = redacted["claims"][0]["sentence_sha256"]
            redacted["claims"][0]["sentence_sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
            with self.assertRaises(ValueError):
                annotation.hydrate_record(redacted, text)

    def test_tampered_surface_hash_always_fails(self):
        text = "oxytocin improved memory"
        redacted = annotation.redact_record(_record(text, 0, 8))
        redacted["mentions"][0]["surface_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            annotation.hydrate_record(redacted, text)

    def test_invalid_span_fails_before_hash_check(self):
        text = "short"
        redacted = annotation.redact_record(_record(text, 0, 5))
        redacted["claims"][0]["start"] = 99
        with self.assertRaises(ValueError):
            annotation.hydrate_record(redacted, text)

    def test_redaction_does_not_mutate_input_and_drops_quant(self):
        text = "oxytocin improved memory"
        record = _record(text, 0, 8)
        record["claims"][0]["quant"] = {"effect_type": "d", "effect_size": 0.4}
        redacted = annotation.redact_record(record)
        self.assertIn("quant", record["claims"][0])
        self.assertNotIn("quant", redacted["claims"][0])
        self.assertIn("sentence", record["claims"][0])


if __name__ == "__main__":
    unittest.main()
