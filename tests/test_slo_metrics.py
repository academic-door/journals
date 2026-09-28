from __future__ import annotations

import unittest

from scripts.build_slo_metrics import build_slo_metrics


class SloMetricsBaselineTests(unittest.TestCase):
    def test_direct_metrics_and_unsupported_lifecycle_ages_remain_distinct(self) -> None:
        completeness = {
            "schema_version": "1.3",
            "reconciliation": {
                "journal_count": 2,
                "complete": 2,
                "partial": 0,
                "not_measured": 0,
                "source_blocked": 0,
            },
            "journals": [
                {
                    "journalKey": "AER",
                    "status": "COMPLETE",
                    "measuredThrough": "2026-09-26",
                    "freshnessStatus": "CURRENT_FOR_AUDIT_END",
                },
                {
                    "journalKey": "JPE",
                    "status": "COMPLETE",
                    "measuredThrough": "2026-09-20",
                    "freshnessStatus": "STALE_FOR_AUDIT_END",
                },
            ],
        }
        monitoring = {
            "summary": {"configured_journals": 2},
            "last_successful_checks": {
                "AER": "2026-09-26T10:00:00+00:00",
                "JPE": "2026-09-26T09:00:00+00:00",
            },
        }
        backfill = {
            "coverage": {
                "publication_ready": 1206,
                "missing": 0,
                "source_pending": 0,
            }
        }

        payload = build_slo_metrics(
            completeness,
            monitoring,
            backfill,
            generated_at="2026-09-26T11:00:00+00:00",
        )

        self.assertEqual("1.0", payload["schema_version"])
        self.assertEqual("baseline_metrics_only", payload["phase"])
        self.assertFalse(payload["alerting_enabled"])
        self.assertFalse(payload["thresholds_defined"])

        expected = payload["metrics"]["expected_set_freshness"]
        self.assertEqual("direct", expected["measurement_state"])
        self.assertEqual(2, expected["measured_count"])
        self.assertEqual(1, expected["current_to_audit_end_count"])
        self.assertEqual(1, expected["stale_for_audit_end_count"])

        probe = payload["metrics"]["monitor_probe_freshness"]
        self.assertEqual("direct", probe["measurement_state"])
        self.assertEqual(2, probe["successful_check_count"])
        self.assertEqual(7200, probe["max_check_age_seconds"])
        self.assertIn("not publisher authority", probe["semantic_scope"])

        self.assertEqual(
            "partial",
            payload["metrics"]["current_issue_authority_freshness"][
                "measurement_state"
            ],
        )
        self.assertEqual(
            "not_yet_measurable",
            payload["metrics"]["official_detection_to_canonical_ready_latency"][
                "measurement_state"
            ],
        )
        self.assertEqual(
            "direct",
            payload["metrics"]["source_pending_age"]["measurement_state"],
        )
        self.assertTrue(payload["metrics"]["source_pending_age"]["age_measurable"])
        self.assertEqual(0, payload["metrics"]["source_pending_age"]["timestamped_count"])
        self.assertEqual(
            "count_only",
            payload["metrics"]["confirmed_missing_age"]["measurement_state"],
        )

    def test_first_party_lifecycle_latency_is_measured_only_for_matching_issue(self) -> None:
        completeness = {
            "reconciliation": {
                "journal_count": 2,
                "complete": 2,
                "partial": 0,
                "not_measured": 0,
                "source_blocked": 0,
            },
            "journals": [
                {
                    "journalKey": "AER",
                    "status": "COMPLETE",
                    "measuredThrough": "2026-09-27",
                    "freshnessStatus": "CURRENT_FOR_AUDIT_END",
                },
                {
                    "journalKey": "JPE",
                    "status": "COMPLETE",
                    "measuredThrough": "2026-09-27",
                    "freshnessStatus": "CURRENT_FOR_AUDIT_END",
                },
            ],
        }
        monitoring = {
            "summary": {"configured_journals": 2},
            "last_successful_checks": {},
            "lifecycle": {
                "AER": {
                    "authority_observed_issue_id": "aer-116-9",
                    "authority_observed_at": "2026-09-27T08:00:00+00:00",
                    "canonical_ready_issue_id": "aer-116-9",
                    "canonical_ready_at": "2026-09-27T10:00:00+00:00",
                },
                "JPE": {
                    "authority_observed_issue_id": "jpe-134-4",
                    "authority_observed_at": "2026-09-27T09:00:00+00:00",
                    "canonical_ready_issue_id": "jpe-134-3",
                    "canonical_ready_at": "2026-09-27T09:30:00+00:00",
                },
            },
        }
        payload = build_slo_metrics(
            completeness,
            monitoring,
            {"coverage": {"publication_ready": 2, "missing": 0, "source_pending": 0}},
            generated_at="2026-09-27T11:00:00+00:00",
        )

        authority = payload["metrics"]["current_issue_authority_freshness"]
        latency = payload["metrics"]["official_detection_to_canonical_ready_latency"]
        self.assertEqual("direct", authority["measurement_state"])
        self.assertEqual(2, authority["measured_count"])
        self.assertEqual("partial", latency["measurement_state"])
        self.assertEqual(2, latency["observed_transition_count"])
        self.assertEqual(1, latency["ready_transition_count"])
        self.assertEqual(7200, latency["min_latency_seconds"])
        self.assertEqual(7200, latency["max_latency_seconds"])

    def test_source_pending_age_uses_only_durable_lifecycle_timestamp(self) -> None:
        completeness = {
            "reconciliation": {
                "journal_count": 1,
                "complete": 0,
                "partial": 0,
                "not_measured": 0,
                "source_blocked": 1,
            },
            "journals": [
                {
                    "journalKey": "AER",
                    "status": "SOURCE_BLOCKED",
                    "measuredThrough": "2026-09-27",
                    "freshnessStatus": "CURRENT_FOR_AUDIT_END",
                }
            ],
        }
        backfill = {
            "coverage": {
                "publication_ready": 0,
                "missing": 0,
                "source_pending": 1,
            },
            "lifecycle": {
                "source_pending": {
                    "aer-116-9": {
                        "since": "2026-09-27T08:00:00+00:00",
                    }
                }
            },
        }
        payload = build_slo_metrics(
            completeness,
            {"summary": {"configured_journals": 1}, "last_successful_checks": {}},
            backfill,
            generated_at="2026-09-27T11:00:00+00:00",
        )
        metric = payload["metrics"]["source_pending_age"]
        self.assertEqual("direct", metric["measurement_state"])
        self.assertEqual(1, metric["open_count"])
        self.assertEqual(1, metric["timestamped_count"])
        self.assertTrue(metric["age_measurable"])
        self.assertEqual(10800, metric["min_age_seconds"])
        self.assertEqual(10800, metric["max_age_seconds"])

    def test_candidate_or_missing_freshness_is_not_promoted(self) -> None:
        completeness = {
            "reconciliation": {
                "journal_count": 2,
                "complete": 1,
                "partial": 0,
                "not_measured": 1,
                "source_blocked": 0,
            },
            "journals": [
                {
                    "journalKey": "AER",
                    "status": "COMPLETE",
                    "measuredThrough": "2026-09-26",
                    "freshnessStatus": "CURRENT_FOR_AUDIT_END",
                },
                {
                    "journalKey": "DEMO",
                    "status": "NOT_MEASURED",
                    "measuredThrough": "",
                    "freshnessStatus": "NOT_MEASURED",
                },
            ],
        }
        payload = build_slo_metrics(
            completeness,
            {"summary": {"configured_journals": 2}, "last_successful_checks": {}},
            {"coverage": {"publication_ready": 1, "missing": 1, "source_pending": 1}},
            generated_at="2026-09-26T11:00:00+00:00",
        )
        expected = payload["metrics"]["expected_set_freshness"]
        self.assertEqual(1, expected["measured_count"])
        self.assertEqual(1, expected["not_measured_count"])
        self.assertEqual(1, payload["metrics"]["source_pending_age"]["open_count"])
        self.assertEqual(1, payload["metrics"]["confirmed_missing_age"]["open_count"])


if __name__ == "__main__":
    unittest.main()
