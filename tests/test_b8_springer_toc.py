from pathlib import Path
import json
import unittest
F = Path(__file__).resolve().parents[1] / "tests/fixtures/b8/springer-source-stage.json"

class SpringerB8TocTests(unittest.TestCase):
    def test_exact_growth_three_and_itpf_not_ready(self):
        source = json.loads(F.read_text(encoding="utf-8"))
        growth, tax = source["journals"]
        self.assertEqual(("JEGROWTH", "jegrowth-31-3"), (growth["journal"],growth["issue_id"]))
        self.assertTrue(growth["verified_full_displayed_toc"])
        self.assertEqual(3, growth["source_item_count"])
        self.assertEqual([1,2,3], [x["source_sequence"] for x in growth["source_items"]])
        self.assertEqual(3, len({x["doi"] for x in growth["source_items"]}))
        self.assertTrue(all(x["publisher_type"] == "OriginalPaper" for x in growth["source_items"]))
        self.assertTrue(all(x["source_url"].endswith(x["doi"]) for x in growth["source_items"]))
        self.assertFalse(growth["enabled"])
        self.assertEqual(("ITPF","itpf-33-5"), (tax["journal"],tax["issue_id"]))
        self.assertFalse(tax["verified_full_displayed_toc"])
        self.assertEqual([],tax["source_items"])
        self.assertFalse(tax["enabled"])
