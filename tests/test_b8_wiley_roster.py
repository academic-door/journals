from pathlib import Path
import json
import unittest
F = Path(__file__).resolve().parents[1] / "tests/fixtures/b8/wiley-jae-41-6-source-stage.json"

class WileyB8IssueStageTests(unittest.TestCase):
    def test_publisher_supplied_order_has_explicit_unverified_fields(self):
        evidence = json.loads(F.read_text(encoding="utf-8"))
        rows = evidence["observed_articles"]
        self.assertEqual("jae-41-6", evidence["issue_id"])
        self.assertEqual(12, evidence["observed_article_count"])
        self.assertEqual(list(range(1,13)), [x["source_sequence"] for x in rows])
        self.assertEqual("731-744", rows[0]["page_range"])
        self.assertEqual("900-906", rows[-1]["page_range"])
        self.assertEqual(12, len({x["title_en"] for x in rows}))
        # DOI/order is publisher-sourced, not inferred from online-first dates or RePEc.
        expected_dois = [
            "10.1002/jae.70067", "10.1002/jae.70061",
            "10.1002/jae.70071", "10.1002/jae.70074",
            "10.1002/jae.70076", "10.1002/jae.70078",
            "10.1002/jae.70077", "10.1002/jae.70073",
            "10.1002/jae.70075", "10.1002/jae.70069",
            "10.1002/jae.70064", "10.1002/jae.70072",
        ]
        self.assertEqual(expected_dois, [x["doi"] for x in rows])
        self.assertEqual(12, evidence["verified_first_party_article_doi_count"])
        self.assertEqual(12, len(set(expected_dois)))
        self.assertEqual(
            ["research_article"] * 10 + ["replication"] * 2,
            [x["publisher_section"] for x in rows],
        )
        self.assertTrue(all(x["doi_status"] == "verified_first_party_article"
                            and x["type_status"] == "verified_first_party_issue_toc_section"
                            and x["publisher_article_url"] == "https://onlinelibrary.wiley.com/doi/" + x["doi"]
                            and x["authors_en"]
                            and x["en_abstract_status"] == "visible_first_party_not_ingested"
                            for x in rows))
        self.assertTrue(all(x["eligibility"] == "research_candidate" for x in rows[:10]))
        self.assertTrue(all(x["eligibility"] == "pending_canonical_replication_adjudication" for x in rows[10:]))
        self.assertEqual("Page 729: ISSUE INFORMATION", evidence["official_issue_information_excluded"])
        self.assertEqual("https://onlinelibrary.wiley.com/toc/10991255/2026/41/6", evidence["publisher_issue_index"])
        self.assertTrue(evidence["first_party_full_toc_verified"])
        self.assertFalse(evidence["doi_type_abstract_translation_verified"])
        self.assertFalse(evidence["enabled"])
