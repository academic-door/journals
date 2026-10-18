"""Land Economics 102(4) official UW Press source/DOI/type regressions."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.import_official_roster_evidence import validate_evidence
from scripts.uwpress_current_roster import apply_landecon_current_roster
from scripts.update_journals import ROOT, validate_issue

PATH = "data/provenance/official-rosters/landecon/landecon-102-4.json"
CONFIG = {"id": "landecon", "official_roster_evidence_path": PATH}


class Landecon1024SourceTests(unittest.TestCase):
    def setUp(self):
        self.ev = json.loads((ROOT / PATH).read_text(encoding="utf-8"))
        self.dois = [v["doi"] for v in self.ev["items"]]
        self.issue = {
            "schema_version": "1.0", "journal_id": "landecon", "journal_name": "Land Economics",
            "issue_id": "landecon-102-4", "volume": "102", "issue": "4",
            "publication_date": "October 2026", "source_url": "https://le.uwpress.org/content/current",
            "retrieved_at": "2026-10-18T17:08:13+00:00",
            "status": "incomplete", "expected_article_count": 0, "research_article_count": 0,
            "articles": [], "quality": {"flags": ["crossref_provisional_roster"],
                                         "excluded_items": [], "duplicate_count": 0},
        }

    def test_publisher_8_complete_first_party_doIs_abstracts(self):
        validate_evidence(self.ev)
        self.assertEqual("https://le.uwpress.org/content/102/4", self.ev["official_url"])
        self.assertEqual(8, len(self.ev["items"]))
        self.assertEqual(0, len(self.ev["excluded_items"]))
        self.assertEqual(list(range(1, 9)), [r["source_sequence"] for r in self.ev["items"]])
        self.assertEqual(8, len(set(self.dois)))
        for row in self.ev["items"]:
            self.assertTrue(row["authors"])
            self.assertGreater(len(row["abstract_en"]), 200)
            self.assertTrue(row["source_url"].startswith("https://le.uwpress.org/content/102/4/"))

    def test_restored_official_source_stays_translation_gated(self):
        before = copy.deepcopy(self.issue)
        issue = apply_landecon_current_roster(self.issue, CONFIG, detected_dois=self.dois)
        self.assertEqual(before, self.issue)
        self.assertEqual(8, len(issue["articles"]))
        self.assertEqual(self.dois, [r["doi"] for r in issue["articles"]])
        self.assertEqual("official_verified", issue["source_status"])
        self.assertEqual("translation_partial", issue["publication_state"])
        self.assertEqual(8, issue["quality"]["abstract_en_complete"])
        self.assertEqual(8, issue["content_counts"]["research_articles"])
        self.assertEqual("official-publisher-issue-full", issue["quality"]["roster_match_scope"])
        validate_issue(issue)

    def test_missing_or_extra_doi_is_denied(self):
        with self.assertRaisesRegex(ValueError, "DOI identities"):
            apply_landecon_current_roster(self.issue, CONFIG, detected_dois=self.dois[:-1])
        with self.assertRaisesRegex(ValueError, "DOI identities"):
            apply_landecon_current_roster(self.issue, CONFIG, detected_dois=self.dois + ["10.3368/fake"])
        extra = copy.deepcopy(self.issue)
        extra["articles"] = [{"doi": "10.3368/fake"}]
        with self.assertRaisesRegex(ValueError, "unverified"):
            apply_landecon_current_roster(extra, CONFIG, detected_dois=self.dois)

    def test_other_issue_and_other_journal_not_promoted(self):
        other = copy.deepcopy(self.issue)
        other["issue_id"] = "landecon-102-3"
        other["issue"] = "3"
        self.assertEqual(other, apply_landecon_current_roster(other, CONFIG, detected_dois=self.dois))
        wrong = copy.deepcopy(self.issue)
        wrong["journal_id"] = "jeem"
        with self.assertRaisesRegex(ValueError, "another journal"):
            apply_landecon_current_roster(wrong, CONFIG, detected_dois=self.dois)

    def test_tampered_source_doi_order_or_time_denied(self):
        for mutation in (
            lambda e: e["items"][0].update(doi="10.3368/changed"),
            lambda e: e["items"][0].update(source_sequence=2),
            lambda e: e["items"][0].update(source_url="https://doi.org/10.3368/le.102.4.011224-0004R2"),
            lambda e: e.update(captured_at="2999-01-01T00:00:00Z"),
        ):
            with self.subTest(mutation=repr(mutation)):
                ev = copy.deepcopy(self.ev)
                mutation(ev)
                with tempfile.TemporaryDirectory() as folder:
                    p = Path(folder) / PATH
                    p.parent.mkdir(parents=True)
                    p.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        apply_landecon_current_roster(self.issue, CONFIG, root=Path(folder), detected_dois=self.dois)


if __name__ == "__main__":
    unittest.main()
