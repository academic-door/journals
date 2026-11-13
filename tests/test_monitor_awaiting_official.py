from __future__ import annotations

import unittest

from scripts.journal_monitor import (
    _awaiting_backoff_hours,
    _awaiting_status,
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
                    "status": "confirmed",
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

    def test_deep_update_failures_remain_visible_without_false_outage(self) -> None:
        state = {
            "schema_version": "1.0",
            "updated_at": "2026-11-13T01:08:17+00:00",
            "journals": {
                "RFS": {
                    "status": "update_failed",
                    "last_error": "ValueError: provisional Crossref roster requires official confirmation",
                    "awaiting_official_count": 3,
                    "failure_count": 0,
                    "deep_failure_count": 2,
                },
                "LUP": {
                    "status": "update_failed",
                    "last_error": "private publisher text must not be public",
                    "failure_count": 0,
                    "deep_failure_count": 1,
                },
                "EJ": {
                    "status": "unchanged",
                    "last_error": "",
                    "failure_count": 0,
                    "deep_failure_count": 0,
                },
                "EJPE": {
                    "status": "rights_review_pending",
                    "last_error": "B8_NEW_ISSUE_SOURCE_AND_PER_DOI_RIGHTS_REVIEW_REQUIRED",
                    "failure_count": 0,
                    "deep_failure_count": 0,
                },
            },
        }
        result = {
            "unchanged_journals": ["EJ"],
            "pending_journals": [],
            "confirmed_journals": ["RFS", "LUP"],
        }
        public = public_status(state, result)
        self.assertEqual("healthy", public["status"])
        self.assertEqual(0, public["summary"]["failed"])
        self.assertEqual(2, public["summary"]["update_failed"])
        self.assertEqual(["RFS", "LUP"], public["update_failed_journals"])
        self.assertEqual(1, public["summary"]["rights_review_pending"])
        self.assertNotIn("last_error", str(public))
        self.assertNotIn("private publisher text", str(public))

    def test_successful_deep_retry_clears_update_failure_debt(self) -> None:
        state = {
            "updated_at": "2026-11-13T01:08:17+00:00",
            "journals": {
                "RFS": {
                    "status": "unchanged",
                    "failure_count": 0,
                    "deep_failure_count": 0,
                }
            },
        }
        public = public_status(state, {"unchanged_journals": ["RFS"]})
        self.assertEqual(0, public["summary"]["update_failed"])
        self.assertEqual([], public["update_failed_journals"])

    def test_same_candidate_keeps_awaiting_status_across_light_probes(self) -> None:
        previous = {"status": "awaiting_official", "awaiting_official_count": 19}
        self.assertTrue(_awaiting_status(previous, same_deep_candidate=True))
        self.assertFalse(_awaiting_status(previous, same_deep_candidate=False))
        self.assertFalse(_awaiting_status({"status": "confirmed"}, True))

    def test_only_awaiting_keeps_overall_status_healthy(self) -> None:
        state = {
            "schema_version": "1.0",
            "updated_at": "2026-08-03T00:00:00+00:00",
            "journals": {
                "AJAE": {
                    "status": "confirmed",
                    "awaiting_official_count": 19,
                    "awaiting_official_since": "2026-08-01T00:00:00+00:00",
                    "last_error": "ValueError: provisional Crossref roster requires official confirmation",
                    "failure_count": 0,
                    "deep_failure_count": 6,
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
