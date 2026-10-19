"""JEEM 140 real publisher 9 vs old 4 source/translation non-regression tests."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.import_official_roster_evidence import validate_evidence
from scripts.jeem_current_roster import apply_jeem_current_roster
from scripts.update_journals import ROOT, validate_issue

EVIDENCE = "data/provenance/official-rosters/jeem/jeem-140-c.json"
CONFIG = {"id": "jeem", "official_roster_evidence_path": EVIDENCE}


class Jeem140SameVolumeTests(unittest.TestCase):
    def setUp(self):
        self.ev = json.loads((ROOT / EVIDENCE).read_text(encoding="utf-8"))
        self.current = json.loads((ROOT / "public/api/v1/journals/jeem/issues/current.json").read_text(encoding="utf-8"))
        self.old_dois = [x["doi"] for x in self.current["articles"]]

    def test_true_publisher_nine_scholarly_two_excluded(self):
        validate_evidence(self.ev)
        self.assertEqual(9, len(self.ev["items"]))
        self.assertEqual(2, len(self.ev["excluded_items"]))
        self.assertEqual([2, 3, 4, 5, 6, 7, 8, 9, 11], [x["source_sequence"] for x in self.ev["items"]])
        self.assertEqual([1, 10], [x["source_sequence"] for x in self.ev["excluded_items"]])
        self.assertEqual(11, len({r["doi"] for r in self.ev["items"] + self.ev["excluded_items"]}))
        restored = self.ev["items"][4:]
        self.assertEqual(5, len(restored))
        for row in restored:
            self.assertTrue(row["authors"])
            self.assertGreater(len(row["abstract_en"]), 200)
            self.assertTrue(row["source_url"].startswith("https://www.sciencedirect.com/science/article/pii/"))

    def test_restore_five_and_preserve_four_accepted_translations(self):
        old = copy.deepcopy(self.current)
        issue = apply_jeem_current_roster(self.current, CONFIG, detected_dois=self.old_dois)
        self.assertEqual(old, self.current)
        self.assertEqual(9, len(issue["articles"]))
        self.assertEqual("official_verified", issue["source_status"])
        self.assertEqual("translation_partial", issue["publication_state"])
        self.assertEqual(9, issue["quality"]["abstract_en_complete"])
        self.assertEqual(9, issue["content_counts"]["research_articles"])
        self.assertEqual(1, issue["content_counts"]["editorial_material"])
        self.assertEqual(1, issue["content_counts"]["corrections"])
        self.assertEqual(11, issue["quality"]["official_item_count"])
        self.assertEqual("official-publisher-issue-full", issue["quality"]["roster_match_scope"])
        self.assertNotIn("browser_order_verification", issue["quality"])
        assert_by_doi = {a["doi"]: a for a in issue["articles"]}
        for article in old["articles"]:
            stored = assert_by_doi[article["doi"]]
            self.assertEqual(article["abstract_cn"], stored["abstract_cn"])
            self.assertEqual(article["title_cn"], stored["title_cn"])
        validate_issue(issue)

    def test_not_a_crossref_trigger_but_strict_source(self):
        previous = copy.deepcopy(self.current)
        previous["issue_id"] = "jeem-139-c"
        previous["volume"] = "139"
        self.assertEqual(previous, apply_jeem_current_roster(previous, CONFIG))
        with self.assertRaisesRegex(ValueError, "unobserved"):
            apply_jeem_current_roster(self.current, CONFIG, detected_dois=self.old_dois + ["10.1016/j.jeem.2026.fake"])
        wrong = copy.deepcopy(self.current)
        wrong["journal_id"] = "jie"
        with self.assertRaisesRegex(ValueError, "unrelated journal"):
            apply_jeem_current_roster(wrong, CONFIG)

    def test_tampered_official_doi_pii_order_type_fails_closed(self):
        for mutate in (
            lambda e: e["items"][4].update(doi="10.1016/j.jeem.2026.fake"),
            lambda e: e["items"][4].update(source_sequence=1),
            lambda e: e["items"][4].update(source_id="pii:S0095069626001415"),
            lambda e: e["excluded_items"][0].update(article_type="research-article"),
            lambda e: e.update(captured_at="2999-01-01T00:00:00Z"),
        ):
            with self.subTest(mutate=repr(mutate)):
                ev = copy.deepcopy(self.ev)
                mutate(ev)
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / EVIDENCE
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        apply_jeem_current_roster(self.current, CONFIG, root=Path(folder))


if __name__ == "__main__":
    unittest.main()
