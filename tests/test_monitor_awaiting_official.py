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

    def test_light_probe_keeps_unresolved_deep_failures_visible(self) -> None:
        # Production 2026-11-13: detect_all re-confirmed RFS and LUP and
        # preserved their failed deep retries; neither issue was READY.
        state = {
            "updated_at": "2026-11-13T04:25:16+00:00",
            "journals": {
                "RFS": {
                    "status": "confirmed",
                    "candidate": {"issue_key": "39:11", "fingerprint": "unchanged"},
                    "deep_failure_count": 3,
                    "last_error": "ValueError: provisional Crossref roster requires official confirmation",
                    "failure_count": 0,
                },
                "LUP": {
                    "status": "confirmed",
                    "candidate": {"issue_key": "172:", "fingerprint": "unchanged"},
                    "deep_failure_count": 15,
                    "last_error": "ValueError: abstract_en_incomplete: private publisher detail should not be public",
                    "failure_count": 0,
                },
                "OLD": {
                    "status": "unchanged",
                    "candidate": None,
                    "deep_failure_count": 2,
                    "last_error": "obsolete historical failure",
                    "failure_count": 0,
                },
            },
        }
        status = public_status(state, {"confirmed_journals": ["RFS", "LUP"]})
        self.assertEqual(2, status["summary"]["update_failed"])
        self.assertEqual(["RFS", "LUP"], status["update_failed_journals"])
        self.assertEqual(0, status["summary"]["failed"])
        self.assertEqual("healthy", status["status"])
        self.assertNotIn("private publisher detail", str(status))
        self.assertNotIn("obsolete historical failure", str(status))

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

    def test_future_labelled_candidates_are_separate_from_update_debt(self) -> None:
        state = {
            "updated_at": "2026-11-16T18:36:09+00:00",
            "journals": {
                "RFS": {
                    "status": "update_failed",
                    "candidate": {"publication_date": "2026-11-01"},
                    "last_error": "ValueError: provisional Crossref roster requires official confirmation",
                    "deep_failure_count": 12,
                },
                "JLE": {
                    "status": "update_failed",
                    "candidate": {"publication_date": "2026-10-02"},
                    "last_error": "ValueError: provisional Crossref roster requires official confirmation",
                    "deep_failure_count": 3,
                },
                "LUP": {
                    "status": "update_failed",
                    "candidate": {"publication_date": "2027-01-01"},
                    "last_error": "ValueError: abstract_en_incomplete",
                    "deep_failure_count": 24,
                },
                "JFI": {
                    "status": "rights_review_pending",
                    "candidate": {"publication_date": "2026-10-01"},
                },
                "IJIO": {
                    "status": "rights_review_pending",
                    "candidate": {"publication_date": "2026-12-01"},
                },
                "JIMF": {
                    "status": "rights_review_pending",
                    "candidate": {"publication_date": "2026-12-01"},
                },
                "EJPE": {
                    "status": "rights_review_pending",
                    "candidate": {"publication_date": "2026-12-01"},
                },
                "UNKNOWN": {
                    "status": "candidate",
                    "candidate": {"publication_date": "unverified"},
                },
                "NO_CANDIDATE": {"status": "unchanged"},
            },
        }
        result = public_status(state, {})
        self.assertEqual(3, result["summary"]["update_failed"])
        self.assertEqual(["RFS", "JLE", "LUP"], result["update_failed_journals"])
        self.assertEqual(4, result["summary"]["future_dated_candidates"])
        self.assertEqual(
            ["LUP", "IJIO", "JIMF", "EJPE"],
            result["future_dated_candidate_journals"],
        )
        self.assertEqual(1, result["summary"]["future_dated_update_failed"])
        self.assertEqual(
            ["LUP"], result["future_dated_update_failed_journals"]
        )
        self.assertEqual(0, result["summary"]["failed"])
        self.assertNotIn("last_error", str(result))
        self.assertEqual("healthy", result["status"])

    def test_future_label_equal_today_or_malformed_snapshot_is_not_guessed(self) -> None:
        state = {
            "updated_at": "2026-12-01T23:59:00+00:00",
            "journals": {
                "DUE_TODAY": {
                    "candidate": {"publication_date": "2026-12-01"},
                },
                "FUTURE": {
                    "candidate": {"publication_date": "2027-01-01"},
                },
                "INVALID": {
                    "candidate": {"publication_date": "2026-13-01"},
                },
                "MISSING_DATE": {"candidate": {"issue_key": "100:"}},
            },
        }
        report = public_status(state, {})
        self.assertEqual(["FUTURE"], report["future_dated_candidate_journals"])
        self.assertEqual(1, report["summary"]["future_dated_candidates"])
        state["updated_at"] = "malformed"
        report = public_status(state, {})
        self.assertEqual([], report["future_dated_candidate_journals"])
        self.assertEqual(0, report["summary"]["future_dated_candidates"])
        state["updated_at"] = "2026-11-16T18:36:09"  # tz-naive => fail closed
        report = public_status(state, {})
        self.assertEqual([], report["future_dated_candidate_journals"])

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
