from pathlib import Path
import json
import unittest
F = Path(__file__).resolve().parents[1] / "tests/fixtures/b8/joeg-26-5-official-toc.json"

class OxfordB8TocTests(unittest.TestCase):
    def test_exact_order_and_no_ambiguous_promotion(self):
        data = json.loads(F.read_text(encoding="utf-8"))
        rows = data["source_items"]
        self.assertEqual(("JOEG", "joeg-26-5"), (data["journal"], data["issue_id"]))
        self.assertEqual(10, data["source_item_count"])
        self.assertEqual(list(range(1,11)), [row["source_sequence"] for row in rows])
        self.assertEqual(10, len({row["doi"] for row in rows}))
        self.assertEqual([3,1,5,1], [sum(row["publisher_section"] == typ for row in rows) for typ in ("anniversary_commentary","emerging_voices","original_article","correction")])
        self.assertEqual(4, sum(row["eligibility"] == "pending_type_adjudication" for row in rows))
        self.assertEqual(5, sum(row["eligibility"] == "research_candidate" for row in rows))
        self.assertEqual("10.1093/jeg/lbag061", rows[-1]["doi"])
        self.assertEqual("excluded", rows[-1]["eligibility"])
        self.assertFalse(data["enabled"])
