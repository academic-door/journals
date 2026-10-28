import json
import unittest
from pathlib import Path
import yaml

from collectors.oup import _resolve_journal
from scripts.validate_journal_expansion_b8 import validate_batch

ROOT = Path(__file__).resolve().parents[1]

class B8SourceContractTests(unittest.TestCase):
    def test_batch_sources_are_isolated(self):
        manifest = json.loads((ROOT / "config/journal-expansion-b8.json").read_text())
        live = yaml.safe_load((ROOT / "config/journals.yml").read_text())["journals"]
        result = validate_batch(manifest, live)
        self.assertEqual(8, result["approved_staged"])
        self.assertEqual(2, result["promoted"])
        self.assertEqual(0, result["production_verification_pending"])
        self.assertTrue(live["JEGROWTH"]["enabled"])
        self.assertEqual("springer", live["JEGROWTH"]["collector"])
        self.assertEqual(49, result["existing_live"])

    def test_joeg_uses_existing_oup_family(self):
        spec, url = _resolve_journal("joeg", None)
        self.assertEqual("joeg", spec["journal_id"])
        self.assertEqual("https://academic.oup.com/joeg/issue", url)
