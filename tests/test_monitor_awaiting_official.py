from __future__ import annotations

import unittest

from scripts.journal_monitor import (
    _awaiting_backoff_hours,
    _is_awaiting_official,
    public_status,
)


class AwaitingOfficialTests(unittest.TestCase):
    def test_provisional_crossref_error_is_awaiting_official(self) -> None:
        self.assertTrue(
            _is_awaiting_official(
                "ValueError: provisional Crossref roster requires official confirmation"
            )
        )
        self.assertFalse(_is_awaiting_official("SourceLagError: expected volume"))
        self.assertFalse(
            _is_awaiting_official("translation incomplete: 4/5")
        )

    def test_awaiting_backoff_is_exponential_and_capped(self) -> None:
        self.assertEqual(_awaiting_backoff_hours(1), 1)
        self.assertEqual(_awaiting_backoff_hours(2), 2)
        self.assertEqual(_awaiting_backoff_hours(3), 4)
        self.assertEqual(_awaiting_backoff_hours(5), 16)
        self.assertEqual(_awaiting_backoff_hours(6), 24)
        self.assertEqual(_awaiting_backoff_hours(40), 24)

    def test_public_status_lists_awaiting_separately(self) -> None:
        state = {
            "schema_version": "1.0",
            "updated_at": "2026-08-03T00:00:00+00:00",
            "journals": {
                "AJAE": {
                    "status": "awaiting_official",
                    "awaiting_official_count": 19,
                    "awaiting_official_since": "2026-08-01T00:00:00+00:00",
                    "last_error": "ValueError: provisional Crossref roster requires official confirmation",
                    "failure_count": 0,
                    "deep_failure_count": 6,
                },
                "BROKEN": {
                    "status": "update_failed",
                    "last_error": "boom",
                    "failure_count": 0,
                    "deep_failure_count": 4,
                },
            },
        }
        result = {
            "unchanged_journals": [],
            "pending_journals": [],
            "confirmed_journals": [],
            "alerts": {"newly_alerting": [], "recovered": []},
        }
        status = public_status(state, result)
        self.assertEqual(status["awaiting_official_journals"], ["AJAE"])
        self.assertEqual(status["failed_journals"], ["BROKEN"])
        self.assertEqual(status["summary"]["awaiting_official"], 1)
        self.assertEqual(status["summary"]["failed"], 1)
        self.assertEqual(status["status"], "degraded")

    def test_only_awaiting_keeps_overall_status_healthy(self) -> None:
        state = {
            "schema_version": "1.0",
            "updated_at": "2026-08-03T00:00:00+00:00",
            "journals": {
                "AJAE": {
                    "status": "awaiting_official",
                    "awaiting_official_count": 19,
                    "awaiting_official_since": "2026-08-01T00:00:00+00:00",
                    "last_error": "ValueError: provisional Crossref roster requires official confirmation",
                    "failure_count": 0,
                    "deep_failure_count": 0,
                },
            },
        }
        result = {
            "unchanged_journals": [],
            "pending_journals": [],
            "confirmed_journals": [],
            "alerts": {"newly_alerting": [], "recovered": []},
        }
        status = public_status(state, result)
        self.assertEqual(status["status"], "healthy")
        self.assertEqual(status["failed_journals"], [])
        self.assertEqual(status["awaiting_official_journals"], ["AJAE"])


if __name__ == "__main__":
    unittest.main()
