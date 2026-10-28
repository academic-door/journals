"""ITPF 33(5) bilingual candidate never upgrades RePEc to first-party TOC."""
import copy
import json
from pathlib import Path
import unittest

from scripts.b8_itpf_candidate import assert_source_stage, make_candidate

STAGE = Path(__file__).resolve().parent / "fixtures/b8/springer-itpf-33-5-publisher-series-stage.json"


class ITPFCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = json.loads(STAGE.read_text(encoding="utf-8"))
        cls.rows = assert_source_stage(cls.stage)
        cls.items = [{
            "doi": row["doi"], "title_en": row["title_en"],
            "authors": ["Sample Author", "Other Author"],
            "abstract": "This synthetic economics abstract describes a quantitative "
                        "research question, empirical evidence and results. "
                        "No actual paper content is reproduced in this test.",
            "abstract_source_url": row["repec_record_url"],
        } for row in cls.rows]

    def test_all_eleven_ordered_metadata_and_source_accepted(self):
        candidate = make_candidate(
            self.stage, self.items, captured_at="2026-10-28T09:00:00+00:00",
        )
        self.assertEqual("publisher_verified", candidate["source_status"])
        self.assertNotEqual("official_verified", candidate["source_status"])
        self.assertEqual("translation_partial", candidate["content_status"])
        self.assertEqual(11, len(candidate["articles"]))
        self.assertEqual(
            [r["doi"] for r in self.rows],
            [r["doi"] for r in candidate["articles"]],
        )
        for article, row in zip(candidate["articles"], self.rows, strict=True):
            self.assertEqual("research-article", article["article_type"])
            self.assertEqual(row["repec_record_url"], article["sources"]["abstract_en"])
            self.assertEqual(
                self.stage["publisher_supplied_serial_url"], article["sources"]["roster"]
            )

    def test_reject_missing_reordered_or_laundered_source(self):
        with self.assertRaisesRegex(ValueError, "missing_source_records"):
            make_candidate(self.stage, self.items[:-1], captured_at="2026-10-28T09:00:00+00:00")
        swapped = copy.deepcopy(self.items)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        with self.assertRaisesRegex(ValueError, "order_or_abstract"):
            make_candidate(self.stage, swapped, captured_at="2026-10-28T09:00:00+00:00")
        wrong = copy.deepcopy(self.items)
        wrong[0]["abstract_source_url"] = "https://evil.example/no-source"
        with self.assertRaisesRegex(ValueError, "provenance_mismatch"):
            make_candidate(self.stage, wrong, captured_at="2026-10-28T09:00:00+00:00")
        forged = copy.deepcopy(self.stage)
        forged["first_party_springer_full_toc_verified"] = True
        with self.assertRaisesRegex(ValueError, "unqualified_publisher_series"):
            make_candidate(forged, self.items, captured_at="2026-10-28T09:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
