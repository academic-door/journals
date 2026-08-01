from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.journal_monitor import (
    ALERT_THRESHOLD,
    Candidate,
    detect_all,
    evaluate_observation,
    issue_fingerprint,
    run_deep_updates,
    select_candidate,
)
from scripts.local_authorized_enrichment import build_jobs, run_jobs


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
    volume: str = "10",
    issue: str = "2",
    published: tuple[int, int, int] = (2026, 5, 1),
) -> dict:
    return {
        "DOI": doi,
        "title": [f"Title {doi}"],
        "type": "journal-article",
        "volume": volume,
        "issue": issue,
        "published": {"date-parts": [list(published)]},
    }


class CandidateSelectionTests(unittest.TestCase):
    def test_unchanged_doi_set_does_not_trigger(self) -> None:
        items = [crossref_item("10.1234/a"), crossref_item("10.1234/b")]
        self.assertIsNone(select_candidate(items, BASELINE, today=date(2026, 7, 28)))

    def test_same_issue_new_doi_is_confirmed_immediately(self) -> None:
        items = [
            crossref_item("10.1234/a"),
            crossref_item("10.1234/b"),
            crossref_item("10.1234/c"),
        ]
        candidate = select_candidate(items, BASELINE, today=date(2026, 7, 28))
        self.assertIsNotNone(candidate)
        status, observation = evaluate_observation(candidate, BASELINE, {})
        self.assertEqual("confirmed", status)
        self.assertEqual(["10.1234/c"], observation["candidate"]["unseen_dois"])

    def test_new_volume_is_confirmed_immediately(self) -> None:
        candidate = Candidate(
            issue_key="11:1",
            volume="11",
            issue="1",
            publication_date="2026-07-01",
            dois=("10.1234/c",),
            unseen_dois=("10.1234/c",),
            fingerprint="candidate",
        )
        status, _observation = evaluate_observation(candidate, BASELINE, {})
        self.assertEqual("confirmed", status)

    def test_ambiguous_issue_requires_two_matching_observations(self) -> None:
        candidate = Candidate(
            issue_key="10:S",
            volume="10",
            issue="S",
            publication_date="2026-06-01",
            dois=("10.1234/c",),
            unseen_dois=("10.1234/c",),
            fingerprint="candidate",
        )
        first_status, first = evaluate_observation(candidate, BASELINE, {})
        second_status, second = evaluate_observation(
            candidate,
            BASELINE,
            first,
        )
        self.assertEqual("candidate", first_status)
        self.assertEqual("confirmed", second_status)
        self.assertEqual(2, second["candidate_seen_count"])

    def test_official_rss_corroboration_confirms_ambiguous_issue(self) -> None:
        candidate = Candidate(
            issue_key="10:S",
            volume="10",
            issue="S",
            publication_date="2026-06-01",
            dois=("10.1234/c",),
            unseen_dois=("10.1234/c",),
            fingerprint="candidate",
        )
        status, observation = evaluate_observation(
            candidate,
            BASELINE,
            {},
            rss_dois={"10.1234/c"},
        )
        self.assertEqual("confirmed", status)
        self.assertIn("official_rss", observation["evidence"])

    def test_future_deposit_is_ignored(self) -> None:
        items = [
            crossref_item(
                "10.1234/future",
                volume="12",
                issue="1",
                published=(2027, 2, 1),
            )
        ]
        self.assertIsNone(select_candidate(items, BASELINE, today=date(2026, 7, 28)))

    def test_next_month_issue_is_allowed_for_early_release_publishers(self) -> None:
        items = [
            crossref_item(
                "10.1234/august",
                volume="11",
                issue="",
                published=(2026, 8, 1),
            ),
            crossref_item(
                "10.1234/september",
                volume="12",
                issue="",
                published=(2026, 9, 1),
            ),
        ]
        candidate = select_candidate(
            items,
            BASELINE,
            today=date(2026, 7, 29),
            publication_lead_months=1,
        )
        self.assertIsNotNone(candidate)
        self.assertEqual("11", candidate.volume)
        self.assertEqual("2026-08-01", candidate.publication_date)

    def test_known_official_exclusion_does_not_retrigger_current_issue(self) -> None:
        baseline = copy.deepcopy(BASELINE)
        baseline["quality"] = {
            "excluded_items": [
                {
                    "title_en": "Recommendations for Further Reading",
                    "doi": "10.1234/recommendations",
                }
            ]
        }
        items = [
            crossref_item("10.1234/a"),
            crossref_item("10.1234/b"),
            {
                **crossref_item("10.1234/recommendations"),
                "title": ["Recommendations for Further Reading"],
            },
        ]
        self.assertIsNone(select_candidate(items, baseline, today=date(2026, 7, 28)))

    def test_online_first_items_without_issue_assignment_are_ignored(self) -> None:
        item = crossref_item("10.1234/online-first")
        item["volume"] = ""
        item["issue"] = ""
        item["published"] = {"date-parts": [[2026, 7, 20]]}
        self.assertIsNone(select_candidate([item], BASELINE, today=date(2026, 7, 28)))

    def test_lower_issue_number_cannot_be_newer_in_same_volume(self) -> None:
        baseline = copy.deepcopy(BASELINE)
        baseline["issue"] = "8"
        item = crossref_item(
            "10.1234/lower",
            volume="10",
            issue="7",
            published=(2026, 7, 20),
        )
        self.assertIsNone(select_candidate([item], baseline, today=date(2026, 7, 28)))


class MonitorStateTests(unittest.TestCase):
    def test_failure_alerts_only_on_third_consecutive_failure(self) -> None:
        config = {
            "DEMO": {
                "id": "demo",
                "enabled": True,
                "issn": "0000-0000",
            }
        }
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            state: dict = {"journals": {}}
            for expected in range(1, ALERT_THRESHOLD + 1):
                state, result = detect_all(
                    config,
                    state,
                    crossref_fetcher=lambda _config, _baseline: (_ for _ in ()).throw(
                        RuntimeError("temporary")
                    ),
                )
                self.assertEqual(
                    expected,
                    state["journals"]["DEMO"]["failure_count"],
                )
            self.assertEqual(["DEMO"], result["alerts"]["newly_alerting"])

    def test_success_resets_failure_and_reports_recovery(self) -> None:
        config = {
            "DEMO": {
                "id": "demo",
                "enabled": True,
                "issn": "0000-0000",
            }
        }
        state = {
            "journals": {
                "DEMO": {
                    "failure_count": ALERT_THRESHOLD,
                    "status": "failed",
                }
            }
        }
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            next_state, result = detect_all(
                config,
                state,
                crossref_fetcher=lambda _config, _baseline: [
                    crossref_item("10.1234/a"),
                    crossref_item("10.1234/b"),
                ],
            )
        self.assertEqual(0, next_state["journals"]["DEMO"]["failure_count"])
        self.assertEqual(["DEMO"], result["alerts"]["recovered"])

    def test_disappearing_candidate_clears_deep_backoff(self) -> None:
        config = {
            "DEMO": {
                "id": "demo",
                "enabled": True,
                "issn": "0000-0000",
            }
        }
        state = {
            "journals": {
                "DEMO": {
                    "candidate": {"fingerprint": "old"},
                    "deep_failure_count": ALERT_THRESHOLD,
                    "next_deep_retry_at": "2999-01-01T00:00:00+00:00",
                    "status": "source_lag",
                }
            }
        }
        with patch("scripts.journal_monitor.read_json", return_value=BASELINE):
            next_state, result = detect_all(
                config,
                state,
                crossref_fetcher=lambda _config, _baseline: [
                    crossref_item("10.1234/a"),
                    crossref_item("10.1234/b"),
                ],
            )
        entry = next_state["journals"]["DEMO"]
        self.assertEqual(0, entry["deep_failure_count"])
        self.assertEqual("", entry["next_deep_retry_at"])
        self.assertEqual(["DEMO"], result["alerts"]["recovered"])

    def test_fingerprint_is_order_independent(self) -> None:
        reversed_issue = copy.deepcopy(BASELINE)
        reversed_issue["articles"].reverse()
        self.assertEqual(issue_fingerprint(BASELINE), issue_fingerprint(reversed_issue))

    def test_deep_update_respects_retry_window(self) -> None:
        state = {
            "journals": {
                "DEMO": {
                    "candidate": {"volume": "11", "issue": "1"},
                    "next_deep_retry_at": "2999-01-01T00:00:00+00:00",
                }
            }
        }
        result = {"alerts": {"newly_alerting": [], "recovered": []}}
        failures = run_deep_updates(
            ["DEMO"],
            state,
            result,
            translate=False,
        )
        self.assertEqual(0, failures)
        self.assertEqual("deferred", result["deep_updates"][0]["result"])

    def test_source_lag_keeps_candidate_and_uses_separate_backoff_counter(self) -> None:
        state = {
            "journals": {
                "DEMO": {
                    "candidate": {"volume": "11", "issue": "1"},
                    "failure_count": 0,
                }
            }
        }
        result = {"alerts": {"newly_alerting": [], "recovered": []}}
        report = {
            "results": [
                {
                    "result": "preserved_previous",
                    "error": "SourceLagError: publisher still exposes volume 10",
                }
            ]
        }
        with (
            patch(
                "scripts.journal_monitor.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ),
            patch("scripts.journal_monitor.read_json", return_value=report),
        ):
            failures = run_deep_updates(
                ["DEMO"],
                state,
                result,
                translate=False,
            )
        self.assertEqual(1, failures)
        self.assertEqual("source_lag", state["journals"]["DEMO"]["status"])
        self.assertEqual(1, state["journals"]["DEMO"]["deep_failure_count"])
        self.assertEqual(0, state["journals"]["DEMO"]["failure_count"])
        self.assertEqual("11", state["journals"]["DEMO"]["candidate"]["volume"])

    def test_entitlement_failure_uses_daily_retry_and_explicit_status(self) -> None:
        state = {
            "journals": {
                "DEMO": {
                    "candidate": {"volume": "11", "issue": "1"},
                    "failure_count": 0,
                }
            }
        }
        result = {"alerts": {"newly_alerting": [], "recovered": []}}
        report = {
            "results": [
                {
                    "result": "preserved_previous",
                    "error": "ValueError: abstract_en_incomplete, elsevier_insttoken_required",
                }
            ]
        }
        with (
            patch(
                "scripts.journal_monitor.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ),
            patch("scripts.journal_monitor.read_json", return_value=report),
        ):
            failures = run_deep_updates(
                ["DEMO"], state, result, translate=False
            )
        self.assertEqual(1, failures)
        self.assertEqual(
            "entitlement_blocked", state["journals"]["DEMO"]["status"]
        )
        retry = datetime.fromisoformat(
            state["journals"]["DEMO"]["next_deep_retry_at"]
        )
        self.assertGreater(retry, datetime.now(timezone.utc) + timedelta(hours=23))


class LocalEnrichmentTests(unittest.TestCase):
    def test_only_missing_metadata_becomes_a_job(self) -> None:
        issue = copy.deepcopy(BASELINE)
        issue["articles"][1]["abstract_en"] = ""
        with tempfile.TemporaryDirectory() as directory:
            jobs = build_jobs(issue, Path(directory), limit=5)
        self.assertEqual(["10.1234/b"], [job["doi"] for job in jobs])

    def test_institutional_mode_is_disabled_in_github_actions(self) -> None:
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with self.assertRaisesRegex(RuntimeError, "local-only"):
                run_jobs([], executable="paper-fetch", timeout=1)

    def test_dry_run_manifest_never_contains_absolute_input_path(self) -> None:
        issue = copy.deepcopy(BASELINE)
        issue["articles"][0]["abstract_en"] = ""
        with tempfile.TemporaryDirectory() as directory:
            jobs = build_jobs(issue, Path(directory), limit=1)
            public = {
                "jobs": [
                    {
                        "doi": job["doi"],
                        "artifact": Path(job["output"]).name,
                    }
                    for job in jobs
                ]
            }
        self.assertNotIn(directory, json.dumps(public))


if __name__ == "__main__":
    unittest.main()
