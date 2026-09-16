from __future__ import annotations

import unittest
from pathlib import Path

from scripts.freshness_announcements import (
    announcement_is_newer,
    normalize_announcement,
)

ROOT = Path(__file__).resolve().parents[1]


def ready(issue_id: str, volume: str, issue: str, publication_date: str) -> dict:
    return {
        "issue_id": issue_id,
        "volume": volume,
        "issue": issue,
        "publication_date": publication_date,
        "publication_state": "ready",
    }


def announcement(**overrides: object) -> dict:
    value = {
        "issue_id": "ecta-94-5",
        "volume": "94",
        "issue": "5",
        "issue_label": "Vol. 94 · No. 5",
        "publication_date": "September 2026",
        "source_authority": "first_party",
        "source_kind": "association_announcement",
        "source_url": "https://www.econometricsociety.org/",
        "observed_at": "2026-09-12T12:00:00Z",
    }
    value.update(overrides)
    return value


class AnnouncedIssueContractTests(unittest.TestCase):
    def test_valid_first_party_announcement_is_normalized(self) -> None:
        item = normalize_announcement("ecta", announcement())
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["journal_id"], "ecta")
        self.assertEqual(item["publication_state"], "announced")
        self.assertEqual(item["source_authority"], "first_party")

    def test_non_first_party_or_incomplete_announcement_fails_closed(self) -> None:
        self.assertIsNone(
            normalize_announcement(
                "ecta", announcement(source_authority="metadata_candidate")
            )
        )
        self.assertIsNone(normalize_announcement("ecta", announcement(source_url="")))
        self.assertIsNone(normalize_announcement("ecta", announcement(observed_at="")))

    def test_same_issue_does_not_lead_ready_content(self) -> None:
        item = normalize_announcement("ecta", announcement())
        assert item is not None
        same_ready = ready("ecta-94-5", "94", "5", "September 2026")
        self.assertFalse(announcement_is_newer(item, same_ready, None))

    def test_genuinely_newer_announcement_leads_last_ready_content(self) -> None:
        item = normalize_announcement("ecta", announcement())
        assert item is not None
        last_ready = ready("ecta-94-4", "94", "4", "July 2026")
        self.assertTrue(announcement_is_newer(item, last_ready, None))

    def test_newer_detected_content_prevents_older_announcement_banner(self) -> None:
        item = normalize_announcement(
            "ecta",
            announcement(issue_id="ecta-94-5", volume="94", issue="5"),
        )
        assert item is not None
        last_ready = ready("ecta-94-4", "94", "4", "July 2026")
        detected = ready("ecta-94-6", "94", "6", "November 2026")
        detected["publication_state"] = "enriching"
        self.assertFalse(announcement_is_newer(item, last_ready, detected))

    def test_frontend_surfaces_announcement_without_relabeling_content(self) -> None:
        source = (ROOT / "src" / "components" / "Top5Explorer.astro").read_text(
            encoding="utf-8"
        )
        self.assertIn("latest_announced_issue_id", source)
        self.assertIn("新一期已发布", source)
        self.assertIn("内容整理中", source)
        self.assertIn("最近完整可读", source)


if __name__ == "__main__":
    unittest.main()
