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
        self.assertEqual(3, result["promoted"])
        self.assertEqual(1, result["production_verification_pending"])
        self.assertEqual("partial_rollout_pending_verification", manifest["launch_state"])
        itpf = next(x for x in manifest["journals"] if x["key"] == "ITPF")
        self.assertEqual("activation_pending_production", itpf["rollout_status"])
        self.assertEqual("academic-door/journals/actions/runs/40832087337", itpf["metadata_seed_evidence"])
        jae = next(item for item in manifest["journals"] if item["key"] == "JAE")
        self.assertEqual("production_verified", jae["rollout_status"])
        evidence = jae["production_acceptance"]
        self.assertEqual("SUCCESS_51_OF_51", evidence["public_health_result"])
        self.assertEqual("jae-41-6", evidence["audited_issue"])
        self.assertEqual(12, evidence["rights_withheld_articles"])
        self.assertEqual(0, evidence["abstracts_en_visible"])
        self.assertEqual(0, evidence["translations_cn_visible"])
        self.assertTrue(live["JEGROWTH"]["enabled"])
        self.assertEqual("springer", live["JEGROWTH"]["collector"])
        self.assertEqual(49, result["existing_live"])

    def test_joeg_uses_existing_oup_family(self):
        spec, url = _resolve_journal("joeg", None)
        self.assertEqual("joeg", spec["journal_id"])
        self.assertEqual("https://academic.oup.com/joeg/issue", url)
