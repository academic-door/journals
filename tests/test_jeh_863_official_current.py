"""JEH 86(3) first-party Cambridge source and fail-closed current issue tests."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.cambridge_current_roster import apply_jeh_current_roster
from scripts.import_official_roster_evidence import validate_evidence
from scripts.update_journals import ROOT, validate_issue

PATH = "data/provenance/official-rosters/jeh/jeh-86-3.json"
CONFIG = {"id": "jeh", "official_roster_evidence_path": PATH}


class Jeh863CurrentTests(unittest.TestCase):
    def setUp(self):
        self.ev = json.loads((ROOT / PATH).read_text(encoding="utf-8"))
        self.dois = [x["doi"] for x in self.ev["items"] + self.ev["excluded_items"][:2]]
        self.issue = {
            "schema_version": "1.0", "journal_id": "jeh",
            "journal_name": "The Journal of Economic History",
            "issue_id": "jeh-86-3", "volume": "86", "issue": "3",
            "publication_date": "September 2026",
            "source_url": "https://www.cambridge.org/core/journals/journal-of-economic-history/latest-issue",
            "retrieved_at": "2026-10-18T16:57:00+00:00",
            "expected_article_count": 0, "research_article_count": 0,
            "status": "incomplete", "articles": [],
            "quality": {"flags": ["crossref_provisional_roster", "abstract_en_incomplete"],
                        "excluded_items": [], "duplicate_count": 0},
        }

    def test_exact_8_articles_4_excluded_and_source(self):
        validate_evidence(self.ev)
        self.assertEqual(8, len(self.ev["items"]))
        self.assertEqual(4, len(self.ev["excluded_items"]))
        self.assertEqual(list(range(1, 9)), [r["source_sequence"] for r in self.ev["items"]])
        self.assertEqual([9, 10, 11, 12], [r["source_sequence"] for r in self.ev["excluded_items"]])
        self.assertEqual(12, len(set(r["doi"] for r in self.ev["items"] + self.ev["excluded_items"])))
        for r in self.ev["items"]:
            self.assertTrue(r["authors"])
            self.assertGreater(len(r["abstract_en"]), 210)
            self.assertTrue(r["source_url"].startswith("https://www.cambridge.org/core/journals/"))

    def test_restored_archived_issue_is_translation_gated(self):
        old = copy.deepcopy(self.issue)
        issue = apply_jeh_current_roster(self.issue, CONFIG, detected_dois=self.dois)
        self.assertEqual(old, self.issue)
        self.assertEqual(8, len(issue["articles"]))
        self.assertEqual("official_verified", issue["source_status"])
        self.assertEqual("translation_partial", issue["publication_state"])
        self.assertEqual(8, issue["content_counts"]["research_articles"])
        self.assertEqual(4, issue["content_counts"]["front_matter"])
        self.assertEqual(8, issue["quality"]["abstract_en_complete"])
        self.assertEqual(12, issue["quality"]["official_item_count"])
        self.assertEqual([r["doi"] for r in self.ev["items"]], [r["doi"] for r in issue["articles"]])
        validate_issue(issue)

    def test_bad_candidate_and_wrong_journal_do_not_publish(self):
        with self.assertRaisesRegex(ValueError, "candidate DOI"):
            apply_jeh_current_roster(self.issue, CONFIG, detected_dois=self.dois[:-3])
        with self.assertRaisesRegex(ValueError, "candidate DOI"):
            apply_jeh_current_roster(self.issue, CONFIG, detected_dois=self.dois + ["10.1017/fake"])
        prev = copy.deepcopy(self.issue)
        prev["issue_id"], prev["issue"] = "jeh-86-2", "2"
        self.assertEqual(prev, apply_jeh_current_roster(prev, CONFIG, detected_dois=self.dois))
        wrong = copy.deepcopy(self.issue)
        wrong["journal_id"] = "jf"
        with self.assertRaisesRegex(ValueError, "journal identity"):
            apply_jeh_current_roster(wrong, CONFIG, detected_dois=self.dois)

    def test_tampered_source_excluded_type_or_doi_sequence_is_rejected(self):
        for mutate in (
            lambda e: e["items"][1].update(doi="10.1017/fake"),
            lambda e: e["items"][1].update(source_sequence=4),
            lambda e: e["excluded_items"][0].update(reason="research"),
            lambda e: e["excluded_items"][0].update(article_type="research-article"),
            lambda e: e.update(captured_at="2999-01-01T00:00:00Z"),
        ):
            with self.subTest(mutate=repr(mutate)):
                ev = copy.deepcopy(self.ev)
                mutate(ev)
                with tempfile.TemporaryDirectory() as temp:
                    p = Path(temp) / PATH
                    p.parent.mkdir(parents=True)
                    p.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        apply_jeh_current_roster(self.issue, CONFIG, root=Path(temp), detected_dois=self.dois)


if __name__ == "__main__":
    unittest.main()
