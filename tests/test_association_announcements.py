from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.journal_monitor import detect_all


BASELINE = {
    "issue_id": "ecta-94-4",
    "journal_id": "ecta",
    "volume": "94",
    "issue": "4",
    "publication_date": "July 2026",
    "articles": [{"doi": "10.3982/old", "abstract_en": "A", "authors": ["A"]}],
}


def crossref_item(issue: str = "5") -> dict:
    return {
        "DOI": "10.3982/new",
        "title": ["New paper"],
        "type": "journal-article",
        "volume": "94",
        "issue": issue,
        "published": {"date-parts": [[2026, 9, 1]]},
    }


class AssociationAnnouncementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "ECTA": {
                "id": "ecta",
                "enabled": True,
                "issn": "0012-9682",
                "announcement_source": "econometric_society_volume",
                "announcement_url": "https://www.econometricsociety.org/publications/econometrica/volume/2026",
            }
        }

    @staticmethod
    def association_signal(
        *,
        volume: str = "94",
        issue: str = "5",
        publication_date: str = "September 2026",
        source_url: str = "https://www.econometricsociety.org/publications/econometrica/volume/2026",
    ) -> dict:
        return {
            "volume": volume,
            "issue": issue,
            "publication_date": publication_date,
            "source_kind": "association_announcement",
            "source_url": source_url,
        }

    def test_exact_association_signal_records_announcement(self) -> None:
        signal = self.association_signal()
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            state, result = detect_all(
                self.config,
                {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [crossref_item()],
                issue_signal_fetcher=lambda _config: signal,
            )

        self.assertEqual(["ECTA"], result["confirmed_journals"])
        entry = state["journals"]["ECTA"]
        self.assertIn("association_announcement", entry["evidence"])
        announcement = entry.get("announcement")
        self.assertIsNotNone(announcement)
        assert announcement is not None
        self.assertEqual("ecta-94-5", announcement["issue_id"])
        self.assertEqual("association_announcement", announcement["source_kind"])
        self.assertEqual("first_party", announcement["source_authority"])
        self.assertEqual("September 2026", announcement["publication_date"])

    def test_mismatched_association_signal_fails_closed(self) -> None:
        signal = self.association_signal(issue="4", publication_date="July 2026")
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            state, _result = detect_all(
                self.config,
                {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [crossref_item()],
                issue_signal_fetcher=lambda _config: signal,
            )

        entry = state["journals"]["ECTA"]
        self.assertNotIn("association_announcement", entry["evidence"])
        self.assertNotIn("announcement", entry)

    def test_newer_first_party_signal_announces_without_crossref_candidate(self) -> None:
        signal = self.association_signal()
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            state, result = detect_all(
                self.config,
                {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [],
                issue_signal_fetcher=lambda _config: signal,
            )

        self.assertEqual(["ECTA"], result["unchanged_journals"])
        self.assertEqual([], result["confirmed_journals"])
        entry = state["journals"]["ECTA"]
        self.assertIsNone(entry["candidate"])
        self.assertIn("association_announcement", entry["evidence"])
        announcement = entry.get("announcement")
        self.assertIsNotNone(announcement)
        assert announcement is not None
        self.assertEqual("ecta-94-5", announcement["issue_id"])
        self.assertEqual("September 2026", announcement["publication_date"])
        self.assertEqual("first_party", announcement["source_authority"])

    def test_same_issue_signal_without_crossref_candidate_fails_closed(self) -> None:
        signal = self.association_signal(issue="4", publication_date="July 2026")
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            state, _result = detect_all(
                self.config,
                {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [],
                issue_signal_fetcher=lambda _config: signal,
            )

        entry = state["journals"]["ECTA"]
        self.assertNotIn("association_announcement", entry["evidence"])
        self.assertNotIn("announcement", entry)

    def test_untrusted_signal_without_crossref_candidate_fails_closed(self) -> None:
        signal = self.association_signal(
            source_url="https://example.com/publications/econometrica/volume/2026"
        )
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            state, _result = detect_all(
                self.config,
                {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [],
                issue_signal_fetcher=lambda _config: signal,
            )

        entry = state["journals"]["ECTA"]
        self.assertNotIn("association_announcement", entry["evidence"])
        self.assertNotIn("announcement", entry)


if __name__ == "__main__":
    unittest.main()
