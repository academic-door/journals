from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.journal_monitor import detect_all


BASELINE = {
    "issue_id": "demo-10-2",
    "journal_id": "demo",
    "volume": "10",
    "issue": "2",
    "publication_date": "May 2026",
    "articles": [
        {"doi": "10.1234/a", "abstract_en": "A", "authors": ["A"]},
        {"doi": "10.1234/b", "abstract_en": "B", "authors": ["B"]},
    ],
}


def crossref_item(
    doi: str,
    *,
    volume: str = "11",
    issue: str = "1",
    published: tuple[int, int, int] = (2026, 9, 1),
) -> dict:
    return {
        "DOI": doi,
        "title": [f"Title {doi}"],
        "type": "journal-article",
        "volume": volume,
        "issue": issue,
        "published": {"date-parts": [list(published)]},
    }


class MonitorAnnouncementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "DEMO": {
                "id": "demo",
                "enabled": True,
                "issn": "0000-0000",
                "rss_url": "https://publisher.example/feed.xml",
            }
        }

    def test_official_rss_corroboration_records_first_party_announcement(self) -> None:
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            state, result = detect_all(
                self.config,
                {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [
                    crossref_item("10.1234/new")
                ],
                rss_fetcher=lambda _url: {"10.1234/new"},
            )

        self.assertEqual(["DEMO"], result["confirmed_journals"])
        announcement = state["journals"]["DEMO"].get("announcement")
        self.assertIsNotNone(announcement)
        assert announcement is not None
        self.assertEqual("first_party", announcement["source_authority"])
        self.assertEqual("official_rss", announcement["source_kind"])
        self.assertEqual("https://publisher.example/feed.xml", announcement["source_url"])
        self.assertEqual("demo-11-1", announcement["issue_id"])
        self.assertEqual("11", announcement["volume"])
        self.assertEqual("1", announcement["issue"])
        self.assertEqual("2026-09-01", announcement["publication_date"])
        self.assertTrue(announcement["observed_at"])

    def test_crossref_only_confirmation_never_claims_first_party_announcement(self) -> None:
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            state, result = detect_all(
                self.config,
                {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [
                    crossref_item("10.1234/new")
                ],
                rss_fetcher=lambda _url: set(),
            )

        self.assertEqual(["DEMO"], result["confirmed_journals"])
        self.assertNotIn("announcement", state["journals"]["DEMO"])


if __name__ == "__main__":
    unittest.main()
