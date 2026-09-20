from __future__ import annotations

import unittest

from scripts.repair_history_dates import _align_repec_article_dates


class RepairHistoryDatesTests(unittest.TestCase):
    def test_aligns_only_repec_roster_article_dates_matching_old_issue_date(self) -> None:
        issue = {
            "articles": [
                {
                    "publication_date": "February 2024",
                    "sources": {"roster": "repec-serial-page"},
                },
                {
                    "publication_date": "March 2024",
                    "sources": {"roster": "repec-serial-page"},
                },
                {
                    "publication_date": "February 2024",
                    "sources": {"roster": "publisher-rss"},
                },
            ]
        }
        changed = _align_repec_article_dates(
            issue,
            current="February 2024",
            repaired="November 2024",
        )
        self.assertEqual(1, changed)
        self.assertEqual("November 2024", issue["articles"][0]["publication_date"])
        self.assertEqual("March 2024", issue["articles"][1]["publication_date"])
        self.assertEqual("February 2024", issue["articles"][2]["publication_date"])


if __name__ == "__main__":
    unittest.main()
