from __future__ import annotations

import unittest
from pathlib import Path

from scripts.update_journals import select_display_issue


ROOT = Path(__file__).resolve().parents[1]


def issue(issue_id: str, volume: str, number: str, publication_date: str) -> dict:
    return {
        "issue_id": issue_id,
        "volume": volume,
        "issue": number,
        "publication_date": publication_date,
    }


class FreshnessDisplayInvariantTests(unittest.TestCase):
    def test_same_issue_always_prefers_ready_snapshot(self) -> None:
        ready = issue("jpe-134-8", "134", "8", "August 2026")
        detected = issue("jpe-134-8", "134", "8", "2026-04-02T04:52:53Z")
        self.assertEqual(select_display_issue(detected, ready), "ready")

    def test_older_detected_may_not_regress_newer_ready(self) -> None:
        ready = issue("jie-163-c", "163", "c", "October 2026")
        detected = issue("jie-162-c", "162", "c", "August 2026")
        self.assertEqual(select_display_issue(detected, ready), "ready")

    def test_genuinely_newer_detected_may_lead(self) -> None:
        ready = issue("wd-206-c", "206", "c", "October 2026")
        detected = issue("wd-207-c", "207", "c", "November 2026")
        self.assertEqual(select_display_issue(detected, ready), "detected")

    def test_missing_side_falls_back_to_available_snapshot(self) -> None:
        ready = issue("aer-116-9", "116", "9", "September 2026")
        detected = issue("new-1-1", "1", "1", "September 2026")
        self.assertEqual(select_display_issue(None, ready), "ready")
        self.assertEqual(select_display_issue(detected, None), "detected")
        self.assertEqual(select_display_issue(None, None), "")

    def test_frontend_prefers_explicit_display_contract(self) -> None:
        source = (ROOT / "src" / "components" / "Top5Explorer.astro").read_text(
            encoding="utf-8"
        )
        selector = source.split("const displayIssueUrl", 1)[1].split(";", 1)[0]
        self.assertIn("latest_display_issue_url", selector)
        self.assertLess(
            selector.index("latest_display_issue_url"),
            selector.index("latest_detected_issue_url"),
        )


if __name__ == "__main__":
    unittest.main()
