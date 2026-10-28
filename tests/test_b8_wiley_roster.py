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
        # Exact first-party Wiley article pages verify the first two DOI/type/author rows.
        self.assertEqual(2, evidence["verified_first_party_article_doi_count"])
        self.assertEqual(
            ["10.1002/jae.70067", "10.1002/jae.70061"],
            [x["doi"] for x in rows[:2]],
        )
        self.assertEqual(
            ["731-744", "745-760"],
            [x["page_range"] for x in rows[:2]],
        )
        self.assertTrue(
            all(x["doi_status"] == "verified_first_party_article"
                and x["type_status"] == "verified_first_party_research_article"
                and x["publisher_article_url"] == "https://onlinelibrary.wiley.com/doi/" + x["doi"]
                and len(x["authors_en"]) == 4
                and x["en_abstract_status"] == "visible_first_party_not_ingested"
                for x in rows[:2])
        )
        # Candidate rows 3-12 remain unqualified; neither full TOC nor CN is ready.
        self.assertTrue(all(x["doi"] is None and x["doi_status"].startswith("pending_") for x in rows[2:]))
        self.assertTrue(all(x["type_status"].startswith("pending_") for x in rows[2:]))
        self.assertFalse(evidence["doi_type_abstract_translation_verified"])
        self.assertFalse(evidence["first_party_full_toc_verified"])
        self.assertFalse(evidence["enabled"])
