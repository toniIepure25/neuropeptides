"""Tests for the public documentation and API contract tooling."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDocsLinks(unittest.TestCase):
    def setUp(self):
        self.tool = _load("ntx_docs_links", "scripts/check_docs_links.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "README.md").write_text(
            "# home\n[guide](docs/guide.md)\n![logo](docs/logo.png)\n[web](https://example.org)\n",
            encoding="utf-8",
        )
        (self.root / "docs" / "guide.md").write_text("# guide\n[home](../README.md)\n", encoding="utf-8")
        (self.root / "docs" / "logo.png").write_bytes(b"png")

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_tree(self):
        self.assertEqual(self.tool.check(self.root), [])
        self.assertIn(self.root / "docs" / "guide.md", self.tool.markdown_files(self.root))

    def test_missing_target_reports_line(self):
        (self.root / "docs" / "guide.md").write_text("# guide\n[missing](nope.md)\n", encoding="utf-8")
        errors = self.tool.check(self.root)
        self.assertEqual(len(errors), 1)
        self.assertIn("docs/guide.md:2", errors[0])
        self.assertIn("nope.md", errors[0])

    def test_fenced_code_is_ignored(self):
        (self.root / "docs" / "guide.md").write_text(
            "# guide\n```\n[missing](nope.md)\n```\n[home](../README.md)\n",
            encoding="utf-8",
        )
        self.assertEqual(self.tool.check(self.root), [])

    def test_main_reports_success_and_failure(self):
        self.assertEqual(self.tool.main(["--root", str(self.root)]), 0)
        (self.root / "docs" / "guide.md").write_text("[missing](nope.md)\n", encoding="utf-8")
        self.assertEqual(self.tool.main(["--root", str(self.root)]), 1)


class TestApiContract(unittest.TestCase):
    def setUp(self):
        self.tool = _load("ntx_api_contract", "scripts/check_api_contract.py")

    def test_contract_is_consistent(self):
        self.assertEqual(self.tool.check(), [])
        self.assertEqual(self.tool.main(), 0)


if __name__ == "__main__":
    unittest.main()
