"""B8 release admission: no legacy first-title whitelist or global staging bypass."""
import copy
import json
from pathlib import Path
import unittest

import yaml

from scripts.validate_journal_expansion_b8 import validate_batch

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = "academic-door/journals/actions/runs/999999999"


class B8PerTitleAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline_manifest = json.loads(
            (ROOT / "config/journal-expansion-b8.json").read_text(encoding="utf-8")
        )
        cls.baseline_live = yaml.safe_load(
            (ROOT / "config/journals.yml").read_text(encoding="utf-8")
        )["journals"]
        # Candidate admission tests start from the accepted 50-title state,
        # even when current production has a later B8 title pending.
        cls.baseline_live.pop("JAE", None)
        cls.baseline_live.pop("ITPF", None)
        cls.baseline_live.pop("JOEG", None)
        cls.baseline_live.pop("IJIO", None)
        next(item for item in cls.baseline_manifest["journals"] if item["key"] == "JAE")["rollout_status"] = "not_enabled"
        next(item for item in cls.baseline_manifest["journals"] if item["key"] == "ITPF")["rollout_status"] = "not_enabled"
        next(item for item in cls.baseline_manifest["journals"] if item["key"] == "JOEG")["rollout_status"] = "not_enabled"
        next(item for item in cls.baseline_manifest["journals"] if item["key"] == "IJIO")["rollout_status"] = "not_enabled"
        cls.baseline_manifest["launch_state"] = "partial_rollout"

    def candidate(self, key, collector, issue_url):
        manifest = copy.deepcopy(self.baseline_manifest)
        live = copy.deepcopy(self.baseline_live)
        entry = next(item for item in manifest["journals"] if item["key"] == key)
        entry["rollout_status"] = "activation_pending_production"
        entry["source_status"] = "official_full_issue_source_ready_verified"
        entry["acceptance_evidence"] = ACCEPTANCE  # Synthetic format test, not real CI.
        manifest["launch_state"] = "partial_rollout_pending_verification"
        live[key] = {
            "id": entry["id"], "issn": entry["issn"], "enabled": True,
            "collector": collector, "current_issue_url": issue_url,
        }
        return manifest, live, entry

    def test_jae_can_enter_independent_admission_without_growth_whitelist(self):
        manifest, live, _ = self.candidate(
            "JAE", "wiley",
            "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6",
        )
        result = validate_batch(manifest, live)
        self.assertEqual(2, result["promoted"])
        self.assertEqual(1, result["production_verification_pending"])
        self.assertEqual(49, result["existing_live"])
        self.assertEqual(len(self.baseline_live) + 1, len(live))

    def test_other_publisher_families_can_proceed_independently(self):
        cases = (
            ("JOEG", "oup", "https://academic.oup.com/joeg/issue/26/5"),
            ("ITPF", "springer", "https://link.springer.com/journal/10797/volumes-and-issues/33-5"),
        )
        for key, collector, url in cases:
            with self.subTest(key=key):
                manifest, live, _ = self.candidate(key, collector, url)
                self.assertEqual(2, validate_batch(manifest, live)["promoted"])

    def test_staging_or_crossref_only_roster_never_promotes(self):
        manifest, live, entry = self.candidate(
            "JAE", "wiley", "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6"
        )
        for source in (
            "official_issue_list_observed",
            "crossref_provisional_roster",
            "publisher_repec_series_observed_first_party_issue_unverified",
        ):
            with self.subTest(source=source):
                entry["source_status"] = source
                with self.assertRaisesRegex(ValueError, "staging"):
                    validate_batch(manifest, live)

    def test_rejects_bad_collector_missing_run_or_unpinned_page(self):
        manifest, live, entry = self.candidate(
            "JAE", "wiley", "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6"
        )
        live["JAE"]["collector"] = "crossref"
        with self.assertRaisesRegex(ValueError, "collector"):
            validate_batch(manifest, live)
        live["JAE"]["collector"] = "wiley"
        entry["acceptance_evidence"] = "fake-success"
        with self.assertRaisesRegex(ValueError, "acceptance"):
            validate_batch(manifest, live)
        entry["acceptance_evidence"] = ACCEPTANCE
        for url in (
            "https://onlinelibrary.wiley.com/journal/10991255",
            "https://evil.example/toc/10991255/2026/41/6",
            "https://onlinelibrary.wiley.com/toc/10991255/2026/41/5",
        ):
            with self.subTest(url=url):
                live["JAE"]["current_issue_url"] = url
                with self.assertRaises(ValueError):
                    validate_batch(manifest, live)

    def test_original_growth_admission_is_not_mutated(self):
        manifest = copy.deepcopy(self.baseline_manifest)
        live = copy.deepcopy(self.baseline_live)
        entry = next(item for item in manifest["journals"] if item["key"] == "JEGROWTH")
        entry["acceptance_evidence"] = ACCEPTANCE
        with self.assertRaisesRegex(ValueError, "original independently accepted"):
            validate_batch(manifest, live)


if __name__ == "__main__":
    unittest.main()
