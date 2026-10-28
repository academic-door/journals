"""ITPF 33(5): publisher-supplied 11-item sequence, NOT first-party TOC."""
import json
from pathlib import Path
import unittest

F = Path(__file__).resolve().parent / "fixtures/b8/springer-itpf-33-5-publisher-series-stage.json"
DOIS = (
    "10.1007/s10797-026-09955-7",
    "10.1007/s10797-026-09958-4",
    "10.1007/s10797-026-09945-9",
    "10.1007/s10797-026-09944-w",
    "10.1007/s10797-026-09949-5",
    "10.1007/s10797-026-09948-6",
    "10.1007/s10797-025-09942-4",
    "10.1007/s10797-025-09932-6",
    "10.1007/s10797-026-09967-3",
    "10.1007/s10797-026-09962-8",
    "10.1007/s10797-025-09928-2",
)


class ITPFPublisherSeriesStageTests(unittest.TestCase):
    def test_exact_11_publisher_supplied_rows_not_false_official(self):
        stage = json.loads(F.read_text(encoding="utf-8"))
        rows = stage["observed_articles"]
        self.assertEqual("itpf-33-5", stage["issue_id"])
        self.assertEqual(11, stage["observed_article_count"])
        self.assertEqual(list(range(1, 12)), [r["source_sequence"] for r in rows])
        self.assertEqual(DOIS, tuple(r["doi"] for r in rows))
        self.assertEqual(11, len(set(DOIS)))
        pages = [tuple(map(int, r["page_range"].split("-"))) for r in rows]
        self.assertEqual(1501, pages[0][0])
        self.assertEqual(1827, pages[-1][1])
        self.assertTrue(all(a[1] + 1 == b[0] for a, b in zip(pages, pages[1:])))
        self.assertEqual("https://ideas.repec.org/s/kap/itaxpf.html",
                         stage["publisher_supplied_serial_url"])
        self.assertTrue(stage["verified_publisher_supplied_11_item_sequence"])
        self.assertFalse(stage["first_party_springer_full_toc_verified"])
        self.assertFalse(stage["doi_type_abstract_translation_verified"])
        self.assertFalse(stage["enabled"])
        self.assertTrue(all(
            x["publisher_article_url"] == "https://link.springer.com/article/" + x["doi"]
            and x["type_status"] == "research_candidate_not_first_party_type_verified"
            for x in rows
        ))


if __name__ == "__main__":
    unittest.main()
