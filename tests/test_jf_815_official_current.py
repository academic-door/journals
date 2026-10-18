"""JF 81(5) pinned first-party AFA source, taxonomy and negative-gate acceptance."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.afa_current_roster import apply_jf_current_roster
from scripts.import_official_roster_evidence import validate_evidence
from scripts.update_journals import ROOT, validate_issue

PATH = "data/provenance/official-rosters/jf/jf-81-5.json"
OFFICIAL = "https://afajof.org/issue/volume-81-issue-5/"
CONFIG = {"id": "jf", "name": "The Journal of Finance", "official_roster_evidence_path": PATH}


class Jf815OfficialCurrentTests(unittest.TestCase):
    def setUp(self):
        self.evidence = json.loads((ROOT / PATH).read_text(encoding="utf-8"))
        self.dois = [x["doi"] for x in self.evidence["items"]] + ["10.1111/jofi.70063"]
        self.issue = {
            "schema_version": "1.0", "journal_id": "jf", "journal_name": "The Journal of Finance",
            "issue_id": "jf-81-5", "volume": "81", "issue": "5",
            "publication_date": "October 2026", "source_url": "https://onlinelibrary.wiley.com/toc/15406261/current",
            "retrieved_at": "2026-10-18T16:51:20+00:00", "articles": [],
            "status": "incomplete", "expected_article_count": 0, "research_article_count": 0,
            "quality": {"flags": ["crossref_provisional_roster", "abstract_en_incomplete"],
                        "excluded_items": [], "duplicate_count": 0},
        }

    def test_publisher_18_doi_order_and_type_provenance(self):
        validate_evidence(self.evidence)
        self.assertEqual(OFFICIAL, self.evidence["official_url"])
        self.assertEqual(14, len(self.evidence["items"]))
        self.assertEqual(4, len(self.evidence["excluded_items"]))
        self.assertEqual(list(range(1, 15)), [x["sequence"] for x in self.evidence["items"]])
        self.assertEqual(list(range(3, 17)), [x["source_sequence"] for x in self.evidence["items"]])
        self.assertEqual([1, 2, 17, 18], [x["source_sequence"] for x in self.evidence["excluded_items"]])
        self.assertEqual(18, len({x["doi"] for x in self.evidence["items"] + self.evidence["excluded_items"]}))
        for row in self.evidence["items"]:
            self.assertTrue(row["authors"])
            self.assertGreater(len(row["abstract_en"]), 200)
            self.assertEqual(OFFICIAL, row["abstract_source_url"])
        self.assertEqual(["10.1111/jofi.13353", "10.1111/jofi.70063", "10.1111/jofi.70019", "10.1111/jofi.13354"],
                         [x["doi"] for x in self.evidence["excluded_items"]])

    def test_publisher_restores_14_research_and_keeps_4_exclusions(self):
        original = copy.deepcopy(self.issue)
        issue = apply_jf_current_roster(self.issue, CONFIG, detected_dois=self.dois)
        self.assertEqual(original, self.issue)
        self.assertEqual("official_verified", issue["source_status"])
        self.assertEqual("translation_partial", issue["publication_state"])
        self.assertEqual(14, len(issue["articles"]))
        self.assertEqual(14, issue["quality"]["abstract_en_complete"])
        self.assertEqual(4, issue["content_counts"]["front_matter"])
        self.assertEqual(18, issue["quality"]["official_item_count"])
        self.assertEqual([x["doi"] for x in self.evidence["items"]], [x["doi"] for x in issue["articles"]])
        self.assertEqual(list(range(3, 17)), [x["source_sequence"] for x in issue["articles"]])
        self.assertEqual("official-publisher-issue-full", issue["quality"]["roster_match_scope"])
        self.assertNotIn("crossref_provisional_roster", issue["quality"]["flags"])
        validate_issue(issue)

    def test_crossref_missing_research_or_spurious_doi_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "candidate not reconciled"):
            apply_jf_current_roster(self.issue, CONFIG, detected_dois=self.dois[:-2])
        with self.assertRaisesRegex(ValueError, "candidate not reconciled"):
            apply_jf_current_roster(self.issue, CONFIG, detected_dois=self.dois + ["10.1111/jofi.fake"])
        copied = copy.deepcopy(self.issue)
        copied["articles"] = [{"doi": "10.1111/jofi.fake"}]
        with self.assertRaisesRegex(ValueError, "unobserved publisher"):
            apply_jf_current_roster(copied, CONFIG, detected_dois=self.dois)

    def test_does_not_promote_other_issue_or_other_journal(self):
        earlier = copy.deepcopy(self.issue)
        earlier["issue_id"] = "jf-81-4"
        earlier["issue"] = "4"
        self.assertEqual(earlier, apply_jf_current_roster(earlier, CONFIG, detected_dois=self.dois))
        other = copy.deepcopy(self.issue)
        other["journal_id"] = "jeh"
        with self.assertRaises(ValueError):
            apply_jf_current_roster(other, CONFIG, detected_dois=self.dois)

    def test_inventory_source_and_taxonomy_changes_are_not_waived(self):
        for mutate in (
            lambda e: e["items"][0].update(doi="10.1111/jofi.fake"),
            lambda e: e["items"][0].update(source_sequence=1),
            lambda e: e["excluded_items"][1].update(article_type="research-article"),
            lambda e: e["items"][0].update(source_url="https://doi.org/10.1111/jofi.70076"),
            lambda e: e.update(captured_at="2999-01-01T00:00:00Z"),
        ):
            with self.subTest(mutate=repr(mutate)):
                ev = copy.deepcopy(self.evidence)
                mutate(ev)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / PATH
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        apply_jf_current_roster(self.issue, CONFIG, root=Path(directory), detected_dois=self.dois)


if __name__ == "__main__":
    unittest.main()
