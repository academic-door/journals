"""JOEG exact official issue: eight Crossref and one OUP OA abstract route."""
import copy
import json
from pathlib import Path
import unittest

from scripts.b8_joeg_candidate import (
    EXPECTED_DOIS, OA_DOI, OA_TITLE, OFFICIAL,
    official_rows, make_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/b8/joeg-26-5-official-toc.json"


def synthetic_item(doi):
    return {
        "doi": doi,
        "title_en": OA_TITLE if doi == OA_DOI else "Synthetic article with a research design",
        "authors": ["First Author", "Second Author"],
        "abstract_en": "This synthetic academic article describes the research question, "
                       "explains the empirical method and reports significant findings "
                       "with references to regional economic mechanisms.",
        "abstract_source": (
            "https://oup.silverchair-cdn.com/article-minimal/8525017"
            if doi == OA_DOI else "https://api.crossref.org/works/" + doi
        ),
        "article_url": "https://doi.org/" + doi,
        "license_urls": [],
    }


class JOEGCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.entries = [synthetic_item(doi) for doi in EXPECTED_DOIS[:9]]

    def test_original_ten_doi_types_and_one_correction(self):
        self.assertEqual(10, len(official_rows(self.stage)))
        cand, report = make_candidate(
            self.stage, self.entries, captured_at="2026-10-28T10:00:00+00:00",
        )
        self.assertEqual(9, len(cand["articles"]))
        self.assertEqual("joeg-26-5", cand["issue_id"])
        self.assertEqual("official_verified", cand["source_status"])
        self.assertEqual(["comment"] * 3 + ["research-article"] * 6,
                         [a["article_type"] for a in cand["articles"]])
        self.assertEqual(list(EXPECTED_DOIS[:9]), [a["doi"] for a in cand["articles"]])
        self.assertEqual(EXPECTED_DOIS[9], report["excluded_correction"])
        self.assertEqual("https://api.crossref.org/works/10.1093/jeg/lbag036",
                         cand["articles"][0]["sources"]["abstract_en"])
        self.assertEqual(
            "https://oup.silverchair-cdn.com/article-minimal/8525017",
            cand["articles"][8]["sources"]["abstract_en"],
        )
        self.assertTrue(all(a["sources"]["roster"] == OFFICIAL for a in cand["articles"]))
        self.assertFalse(report["production_writes"])
        self.assertFalse(report["release_enabled"])

    def test_incomplete_or_unqualified_source_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "batch_not_nine"):
            make_candidate(self.stage, self.entries[:-1], captured_at="2026-10-28T10:00:00+00:00")
        wrong = copy.deepcopy(self.entries)
        wrong[2], wrong[3] = wrong[3], wrong[2]
        with self.assertRaisesRegex(ValueError, "doi_or_order"):
            make_candidate(self.stage, wrong, captured_at="2026-10-28T10:00:00+00:00")
        stage = copy.deepcopy(self.stage)
        stage["verified_full_displayed_toc"] = False
        with self.assertRaisesRegex(ValueError, "official_roster_mismatch"):
            make_candidate(stage, self.entries, captured_at="2026-10-28T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
