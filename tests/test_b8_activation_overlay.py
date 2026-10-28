"""Fail-closed 49->50 B8 activation bridge, with no production writes."""
import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml

from scripts.b8_activation_overlay import review_activation_transition

ROOT = Path(__file__).resolve().parents[1]


class B8ActivationBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "config/journal-expansion-b8.json").read_text())
        cls.configs = yaml.safe_load((ROOT / "config/journals.yml").read_text())["journals"]

    def test_existing_49_live_is_unmodified(self):
        baseline = copy.deepcopy(self.configs)
        baseline.pop("JEGROWTH", None)
        self.assertEqual(49, len(baseline))
        self.assertEqual(
            "no_op",
            review_activation_transition(self.manifest, baseline, {}, None),
        )

    def test_reject_unapproved_50th_title(self):
        conf = copy.deepcopy(self.configs)
        conf["JEGROWTH"] = {"id": "jegrowth", "enabled": True, "collector": "crossref", "current_issue_url": ""}
        with self.assertRaisesRegex(ValueError, "source-qualified"):
            review_activation_transition(self.manifest, conf, {}, None)

    def test_reject_unready_50th_title(self):
        conf = copy.deepcopy(self.configs)
        conf["JEGROWTH"] = {
            "id": "jegrowth", "issn": "1381-4338", "enabled": True, "collector": "springer",
            "current_issue_url": "https://link.springer.com/journal/10887/volumes-and-issues/31-3",
        }
        with self.assertRaisesRegex(ValueError, "lacks"):
            review_activation_transition(self.manifest, conf, {}, None)


    def _pending_jae(self):
        manifest = copy.deepcopy(self.manifest)
        configs = copy.deepcopy(self.configs)
        spec = next(x for x in manifest["journals"] if x["key"] == "JAE")
        spec["rollout_status"] = "activation_pending_production"
        spec["source_status"] = "official_full_issue_verified"
        spec["acceptance_evidence"] = "academic-door/journals/actions/runs/999999999"
        manifest["launch_state"] = "partial_rollout_pending_verification"
        configs["JAE"] = {
            "id": "jae", "issn": "0883-7252", "enabled": True,
            "collector": "wiley",
            "current_issue_url": "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6",
        }
        issue = {
            "journal_id": "jae", "issue_id": "jae-41-6",
            "source_status": "official_verified",
            "publication_state": "ready",
            "quality": {"roster_match": True, "order_preserved": True},
            "articles": [{"doi": "10.1002/jae.70067"}],
        }
        health = {"summary": {"enabled_journals": 50, "available_journals": 50}}
        return manifest, configs, health, issue

    def test_new_title_rebuild_after_real_ready_content_gate(self):
        manifest, configs, health, issue = self._pending_jae()
        with (
            patch("scripts.b8_activation_overlay.validate_issue"),
            patch("scripts.b8_activation_overlay.normalize_issue_content", return_value=issue),
            patch("scripts.b8_activation_overlay.is_archivable_snapshot", return_value=True),
            patch("scripts.b8_activation_overlay.issue_translation_semantics_valid", return_value=True),
        ):
            self.assertEqual("rebuild", review_activation_transition(manifest, configs, health, issue))
            current = {"summary": {"enabled_journals": 51, "available_journals": 51}}
            self.assertEqual("no_op", review_activation_transition(manifest, configs, current, None))

    def test_publisher_verified_itpf_has_same_ready_gate(self):
        manifest, configs, health, issue = self._pending_jae()
        jae = next(x for x in manifest["journals"] if x["key"] == "JAE")
        itpf = next(x for x in manifest["journals"] if x["key"] == "ITPF")
        jae["rollout_status"] = "not_enabled"
        itpf["rollout_status"] = "activation_pending_production"
        itpf["source_status"] = "publisher_verified_serial_issue"
        itpf["acceptance_evidence"] = "academic-door/journals/actions/runs/999999999"
        configs.pop("JAE")
        configs["ITPF"] = {
            "id": "itpf", "issn": "0927-5940", "enabled": True,
            "collector": "springer",
            "current_issue_url": "https://link.springer.com/journal/10797/volumes-and-issues/33-5",
        }
        issue["journal_id"] = "itpf"
        issue["issue_id"] = "itpf-33-5"
        issue["source_status"] = "publisher_verified"
        with (
            patch("scripts.b8_activation_overlay.validate_issue"),
            patch("scripts.b8_activation_overlay.normalize_issue_content", return_value=issue),
            patch("scripts.b8_activation_overlay.is_archivable_snapshot", return_value=True),
            patch("scripts.b8_activation_overlay.issue_translation_semantics_valid", return_value=True),
        ):
            self.assertEqual("rebuild", review_activation_transition(manifest, configs, health, issue))
            issue["publication_state"] = "source_pending"
            with self.assertRaisesRegex(ValueError, "READY"):
                review_activation_transition(manifest, configs, health, issue)

    def test_new_title_rejects_source_pending_or_missing_translations(self):
        manifest, configs, health, issue = self._pending_jae()
        with patch("scripts.b8_activation_overlay.validate_issue"), patch(
            "scripts.b8_activation_overlay.normalize_issue_content", return_value=issue
        ), patch("scripts.b8_activation_overlay.is_archivable_snapshot", return_value=True), patch(
            "scripts.b8_activation_overlay.issue_translation_semantics_valid", return_value=True
        ):
            issue["source_status"] = "source_pending"
            with self.assertRaisesRegex(ValueError, "READY"):
                review_activation_transition(manifest, configs, health, issue)
            issue["source_status"] = "official_verified"
            issue["publication_state"] = "translation_partial"
            with self.assertRaisesRegex(ValueError, "READY"):
                review_activation_transition(manifest, configs, health, issue)

    def test_requires_one_title_at_a_time_and_exact_baseline(self):
        manifest, configs, health, issue = self._pending_jae()
        itpf = next(x for x in manifest["journals"] if x["key"] == "ITPF")
        itpf["rollout_status"] = "activation_pending_production"
        with self.assertRaisesRegex(ValueError, "only one B8"):
            from scripts.b8_activation_overlay import pending_promotion
            pending_promotion(manifest)
        itpf["rollout_status"] = "not_enabled"
        health["summary"]["enabled_journals"] = 48
        with self.assertRaisesRegex(ValueError, "baseline"):
            review_activation_transition(manifest, configs, health, issue)


if __name__ == "__main__":
    unittest.main()
