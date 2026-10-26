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
        self.assertTrue(all(x["doi"] is None and x["doi_status"].startswith("pending_") for x in rows))
        self.assertTrue(all(x["type_status"].startswith("pending_") for x in rows))
        self.assertFalse(evidence["first_party_full_toc_verified"])
        self.assertFalse(evidence["enabled"])
