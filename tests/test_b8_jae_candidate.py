"""Offline proof that the JAE content route uses real-style DOI records, not mock HTML."""
import copy
import json
from pathlib import Path
import unittest

from scripts.b8_jae_candidate import make_candidate, make_report
from collectors.jae_crossref_audit import CROSSREF_ENDPOINT

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "tests/fixtures/b8/wiley-jae-41-6-source-stage.json"


def deposited(row):
    return {
        "DOI": row["doi"],
        "publisher": "Wiley",
        "container-title": ["Journal of Applied Econometrics"],
        "volume": "41", "issue": "6",
        "title": [row["title_en"]],
        "author": [
            {"given": name.rsplit(" ", 1)[0], "family": name.rsplit(" ", 1)[1]}
            for name in row["authors_en"]
        ],
        "abstract": "<jats:p>This is a synthetic English abstract about an economic "
                    "method and its findings. It supplies enough prose to test "
                    "content materialization and never represents a real paper.</jats:p>",
        "license": [{"URL": "https://onlinelibrary.wiley.com/termsAndConditions#vor"}],
    }


class JAECandidateMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = json.loads(STAGE.read_text(encoding="utf-8"))
        cls.deposit = [deposited(row) for row in cls.stage["observed_articles"]]

    def test_12_crossref_abstracts_appear_in_nonproduction_candidate(self):
        candidate, evidence = make_candidate(
            self.stage, self.deposit, retrieved_at="2026-10-28T08:00:00+00:00",
        )
        self.assertEqual("jae-41-6", candidate["issue_id"])
        self.assertEqual(12, len(candidate["articles"]))
        self.assertEqual("official_verified", candidate["source_status"])
        self.assertEqual(
            [x["doi"] for x in self.stage["observed_articles"]],
            [x["doi"] for x in candidate["articles"]],
        )
        self.assertTrue(all(
            x["sources"]["abstract_en"] == CROSSREF_ENDPOINT + x["doi"]
            and x["sources"]["roster"] == self.stage["publisher_issue_index"]
            and x["source_url"].startswith("https://onlinelibrary.wiley.com/doi/")
            and x["abstract_en"].startswith("This is a synthetic")
            and x["article_type"] == "research-article"
            for x in candidate["articles"]
        ))
        self.assertEqual(12, len(evidence["articles"]))
        self.assertTrue(all(
            not x["abstract_reuse_rights_verified"]
            and not x["eligible_for_activation"]
            for x in evidence["articles"]
        ))
        report = make_report(candidate, evidence, translation_report=None)
        self.assertFalse(report["production_release_approved"])
        self.assertFalse(report["production_writes"])
        self.assertFalse(report["content_quality_ready"])

    def test_missing_swapped_or_wrong_volume_deposit_fails(self):
        with self.assertRaisesRegex(ValueError, "incomplete_deposit_batch"):
            make_candidate(self.stage, self.deposit[:-1], retrieved_at="2026-10-28T08:00:00+00:00")
        swap = copy.deepcopy(self.deposit)
        swap[0], swap[1] = swap[1], swap[0]
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            make_candidate(self.stage, swap, retrieved_at="2026-10-28T08:00:00+00:00")
        wrong = copy.deepcopy(self.deposit)
        wrong[0]["volume"] = "40"
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            make_candidate(self.stage, wrong, retrieved_at="2026-10-28T08:00:00+00:00")

    def test_never_changes_unverified_official_stage(self):
        stage = copy.deepcopy(self.stage)
        stage["enabled"] = True
        with self.assertRaisesRegex(ValueError, "official_roster_mismatch"):
            make_candidate(stage, self.deposit, retrieved_at="2026-10-28T08:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
