"""Fail-closed B8 growth pilot regressions; no network or translation spend."""
import copy
import json
import unittest
from pathlib import Path

from scripts.b8_springer_pilot import (
    MANIFEST, FIXTURE, approved_pilot, verify_capture, make_candidate,
)

class B8SpringerPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(Path(MANIFEST).read_text(encoding="utf-8"))
        cls.stage = json.loads(Path(FIXTURE).read_text(encoding="utf-8"))
        cls.spec, cls.source = approved_pilot(cls.stage, cls.manifest)

    def evidence(self):
        return {
            "schema_version": "1.0",
            "capture_mode": "official-roster-evidence",
            "method": "official-page-read",
            "captured_at": "2026-10-26T09:00:00+00:00",
            "finalized": True,
            "journal_id": "jegrowth",
            "issue_id": "jegrowth-31-3",
            "official_url": self.source["official_url"],
            "excluded_item_count": 0,
            "excluded_items": [],
            "items": [
                {
                    "sequence": i,
                    "doi": row["doi"],
                    "title_en": row["title_en"].rstrip("*"),
                    "authors": ["Author Example"],
                    "abstract_en": "This abstract is a test-only example illustrating evidence and issue " * 2,
                    "source_url": row["source_url"],
                }
                for i, row in enumerate(self.source["source_items"], start=1)
            ],
        }

    def test_exact_approved_immutable_stage(self):
        self.assertEqual(3, len(self.source["source_items"]))
        self.assertEqual("not_enabled", self.spec["rollout_status"])

    def test_reject_publisher_order_change(self):
        evidence = self.evidence()
        evidence["items"][0]["doi"], evidence["items"][1]["doi"] = (
            evidence["items"][1]["doi"], evidence["items"][0]["doi"]
        )
        with self.assertRaisesRegex(ValueError, "sequence changed"):
            verify_capture(evidence, self.source)

    def test_reject_truncated_publisher_toc(self):
        evidence = self.evidence()
        evidence["items"].pop()
        with self.assertRaisesRegex(ValueError, "length changed"):
            verify_capture(evidence, self.source)

    def test_reject_abstract_metadata_failure(self):
        evidence = self.evidence()
        evidence["items"][1]["abstract_en"] = "stub"
        with self.assertRaisesRegex(ValueError, "short official abstract"):
            verify_capture(evidence, self.source)

    def test_reject_activation_without_independent_release(self):
        manifest = copy.deepcopy(self.manifest)
        next(x for x in manifest["journals"] if x["key"] == "JEGROWTH")["rollout_status"] = "enabled"
        with self.assertRaisesRegex(ValueError, "must not be production-enabled"):
            approved_pilot(self.stage, manifest)

    def test_canonical_issue_is_source_verified_but_not_translation_ready(self):
        evidence = self.evidence()
        verify_capture(evidence, self.source)
        candidate = make_candidate(evidence, self.spec, self.source)
        self.assertEqual("official_verified", candidate["source_status"])
        self.assertEqual(3, candidate["research_article_count"])
        self.assertNotEqual("ready", candidate["publication_state"])
        self.assertEqual(
            [row["doi"] for row in self.source["source_items"]],
            [row["doi"] for row in candidate["articles"]],
        )

if __name__ == "__main__":
    unittest.main()
