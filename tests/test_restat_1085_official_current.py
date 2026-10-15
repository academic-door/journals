"""Bounded authoritative RESTAT 108(5) source, taxonomy and publication gates."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from collectors.article_types import canonical_article_type, official_no_abstract_exception
from scripts.import_official_roster_evidence import validate_evidence
from scripts.restat_current_roster import apply_restat_current_roster
from scripts.update_journals import ROOT, validate_issue


PATH = "data/provenance/official-rosters/restat/restat-108-5.json"
CONFIG = {
    "id": "restat",
    "name": "The Review of Economics and Statistics",
    "collector": "crossref",
    "current_issue_url": "https://direct.mit.edu/rest/issue",
    "official_roster_evidence_path": PATH,
}


class Restat1085OfficialCurrentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = json.loads((ROOT / PATH).read_text(encoding="utf-8"))
        self.dois = [r["doi"] for r in self.evidence["items"] + self.evidence["excluded_items"]]
        self.issue = {
            "schema_version": "1.0", "journal_id": "restat",
            "journal_name": "The Review of Economics and Statistics",
            "issue_id": "restat-108-5", "volume": "108", "issue": "5",
            "publication_date": "September 2026",
            "source_url": "https://direct.mit.edu/rest/issue",
            "retrieved_at": "2026-10-15T08:05:27+00:00",
            "expected_article_count": 0, "research_article_count": 0,
            "status": "incomplete", "articles": [],
            "quality": {"flags": ["crossref_provisional_roster", "abstract_en_incomplete"],
                        "excluded_items": [], "duplicate_count": 0},
        }

    def apply(self, issue=None, detected=None, config=None):
        return apply_restat_current_roster(
            issue if issue is not None else self.issue,
            config if config is not None else CONFIG,
            detected_dois=self.dois if detected is None else detected,
        )

    def with_mutation(self, modify, issue=None):
        data = copy.deepcopy(self.evidence)
        modify(data)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / PATH
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return apply_restat_current_roster(
                issue if issue is not None else self.issue,
                CONFIG, root=Path(directory), detected_dois=self.dois,
            )

    def test_immutable_24_exact_first_party_rows_and_english_abstracts(self):
        validate_evidence(self.evidence)
        self.assertEqual("official-page-read", self.evidence["method"])
        self.assertEqual("https://direct.mit.edu/rest/issue/108/5", self.evidence["official_url"])
        self.assertEqual(23, len(self.evidence["items"]))
        self.assertEqual(1, len(self.evidence["excluded_items"]))
        self.assertEqual(list(range(1, 24)), [x["sequence"] for x in self.evidence["items"]])
        self.assertEqual(24, len(set(self.dois)))
        self.assertEqual(22, sum(bool(x.get("abstract_en")) for x in self.evidence["items"]))
        self.assertEqual(["Article"] * 21 + ["Comment", "Response"],
                         [x["entry_type"] for x in self.evidence["items"]])
        self.assertEqual("10.1162/rest.x.1827", self.evidence["excluded_items"][0]["doi"])
        self.assertEqual(24, self.evidence["excluded_items"][0]["source_sequence"])
        for row in self.evidence["items"] + self.evidence["excluded_items"]:
            self.assertTrue(row["source_url"].startswith("https://direct.mit.edu/rest/article/108/5/"))
        self.assertTrue(self.evidence["capture_reference"].startswith("owner-first-party-doi-order:"))

    def test_restored_issue_source_verified_but_not_translation_ready(self):
        before = copy.deepcopy(self.issue)
        issue = self.apply()
        self.assertEqual(self.issue, before)
        self.assertEqual([x["doi"] for x in self.evidence["items"]],
                         [x["doi"] for x in issue["articles"]])
        self.assertEqual(23, len(issue["articles"]))
        self.assertEqual(24, issue["quality"]["official_item_count"])
        self.assertEqual("official_verified", issue["source_status"])
        self.assertEqual("translation_partial", issue["publication_state"])
        self.assertEqual(21, issue["content_counts"]["research_articles"])
        self.assertEqual(2, issue["content_counts"]["comments"])
        self.assertEqual(1, issue["content_counts"]["corrections"])
        self.assertEqual(23, issue["quality"]["abstract_en_complete"])
        self.assertNotIn("crossref_provisional_roster", issue["quality"]["flags"])
        self.assertEqual("official-publisher-issue-full", issue["quality"]["roster_match_scope"])
        validate_issue(issue)

    def test_response_is_a_comment_with_truthful_no_abstract_exception(self):
        response = self.evidence["items"][22]
        self.assertEqual("10.1162/rest.x.1825", response["doi"])
        self.assertEqual("", response["abstract_en"])
        self.assertEqual("comment", canonical_article_type(response["title_en"], "research-article"))
        self.assertIsNotNone(official_no_abstract_exception("restat-108-5", response["doi"]))
        issue = self.apply()
        article = issue["articles"][22]
        self.assertEqual("comment", article["article_type"])
        self.assertEqual("", article["abstract_en"])
        self.assertEqual("", article["abstract_cn"])
        self.assertTrue(article["title_cn"])
        self.assertEqual("official_not_provided", article["abstract_status"])
        self.assertEqual("complete", article["translation"]["status"])
        self.assertIsNone(official_no_abstract_exception("restat-108-5", "10.1162/rest_a_01467"))
        self.assertEqual("research-article", canonical_article_type("Random research article", "research-article"))

    def test_candidate_exact_doi_set_must_match_publisher_not_crossref_order(self):
        with self.assertRaisesRegex(ValueError, "detected DOI identities contradict"):
            self.apply(detected=self.dois[:-1])
        with self.assertRaisesRegex(ValueError, "detected DOI identities contradict"):
            self.apply(detected=self.dois[:-1] + ["10.1162/fabricated"])
        issue = copy.deepcopy(self.issue)
        issue["articles"] = [{"doi": "10.1162/fabricated"}]
        with self.assertRaisesRegex(ValueError, "non-publisher DOI"):
            self.apply(issue)

    def test_no_waiver_for_changed_type_exclusion_timestamp_source(self):
        for mutate, reason in (
            (lambda e: e["items"][22].update(entry_type="Article"), "section taxonomy"),
            (lambda e: e["excluded_items"][0].update(reason="publishable"), "correction"),
            (lambda e: e.update(captured_at="2999-01-01T00:00:00Z"), "timestamp"),
            (lambda e: e["items"][3].update(source_url="https://doi.org/10.1162/rest_a_01468"), "publisher page"),
            (lambda e: e["items"][3].update(doi=e["items"][2]["doi"]), "duplicate DOI"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    self.with_mutation(mutate)

    def test_unchanged_other_rest_at_issue_cannot_inherit_old_snapshot(self):
        past = copy.deepcopy(self.issue)
        past["issue_id"] = "restat-108-4"
        past["issue"] = "4"
        self.assertEqual(past, self.apply(issue=past))
        another = copy.deepcopy(self.issue)
        another["journal_id"] = "jf"
        with self.assertRaises(ValueError):
            self.apply(issue=another)
        with self.assertRaisesRegex(ValueError, "provenance root"):
            self.apply(config={**CONFIG, "official_roster_evidence_path": "../wrong.json"})


if __name__ == "__main__":
    unittest.main()
