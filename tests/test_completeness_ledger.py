"""Tests for the corrected 2026 completeness ledger engine (r1 semantics)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_completeness_ledger import build_ledger, inspect_archive


JOURNALS = {
    "AER": {"id": "aer", "name": "American Economic Review"},
    "JPE": {"id": "jpe", "name": "Journal of Political Economy"},
    "QJE": {"id": "qje", "name": "The Quarterly Journal of Economics"},
    "EJ": {"id": "ej", "name": "The Economic Journal"},
}


def state_with_discovery(discovery: dict, issues: dict | None = None) -> dict:
    return {"schema_version": "1.1", "issues": issues or {}, "discovery": discovery}


def archive(journal_id: str, issue_id: str, status: str = "ready") -> dict:
    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": journal_id,
        "volume": "1",
        "issue": "1",
        "status": status,
        "articles": [],
        "quality": {},
    }


def _ledger(tmp: Path, discovery: dict) -> dict:
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps(state_with_discovery(discovery)), encoding="utf-8")
    return build_ledger(state_paths=[state_path], journals=JOURNALS, api_root=tmp / "api")


class CompletenessLedgerR1Tests(unittest.TestCase):
    def test_crossref_candidate_only_is_not_measured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = _ledger(
                root,
                {
                    "QJE": {
                        "issue_ids": ["qje-140-1"],
                        "issue_years": {"qje-140-1": 2026},
                        "authority": "crossref_candidate",
                        "refreshed_at": "2026-09-02T00:00:00+00:00",
                    }
                },
            )
            rec = next(r for r in payload["journals"] if r["journalKey"] == "QJE")
            self.assertEqual("NOT_MEASURED", rec["status"])
            self.assertEqual(0, rec["expectedIssueCount"])

    def test_authoritative_snapshot_with_no_2026_coverage_is_not_measured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = _ledger(
                root,
                {
                    "EJ": {
                        "issue_ids": ["ej-135-1"],
                        "issue_years": {"ej-135-1": 2025},
                        "authority": "official_archive",
                        "refreshed_at": "2026-09-02T00:00:00+00:00",
                    }
                },
            )
            rec = next(r for r in payload["journals"] if r["journalKey"] == "EJ")
            self.assertEqual("NOT_MEASURED", rec["status"])

    def test_authoritative_ready_archive_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"
            api.mkdir(parents=True)
            (api / "aer-116-1.json").write_text(
                json.dumps(archive("aer", "aer-116-1", "ready")), encoding="utf-8"
            )
            payload = _ledger(
                root,
                {
                    "AER": {
                        "issue_ids": ["aer-116-1"],
                        "issue_years": {"aer-116-1": 2026},
                        "issue_refs": {"aer-116-1": {"volume": "116", "issue": "1"}},
                        "authority": "official_archive",
                        "refreshed_at": "2026-09-02T00:00:00+00:00",
                    }
                },
            )
            rec = next(r for r in payload["journals"] if r["journalKey"] == "AER")
            self.assertEqual("COMPLETE", rec["status"])
            self.assertEqual(1, rec["publicationReadyIssueCount"])
            self.assertEqual(0, rec["missingIssueCount"])

    def test_authoritative_missing_archive_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = _ledger(
                root,
                {
                    "AER": {
                        "issue_ids": ["aer-116-1"],
                        "issue_years": {"aer-116-1": 2026},
                        "issue_refs": {"aer-116-1": {"volume": "116", "issue": "1"}},
                        "authority": "official_archive",
                        "refreshed_at": "2026-09-02T00:00:00+00:00",
                    }
                },
            )
            rec = next(r for r in payload["journals"] if r["journalKey"] == "AER")
            self.assertEqual("PARTIAL", rec["status"])
            self.assertEqual(1, rec["missingIssueCount"])
            self.assertEqual(["aer-116-1"], [m["issue_id"] for m in rec["missingIssues"]])

    def test_authoritative_source_pending_archive_is_source_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"
            api.mkdir(parents=True)
            (api / "aer-116-1.json").write_text(
                json.dumps(archive("aer", "aer-116-1", "source_pending")), encoding="utf-8"
            )
            payload = _ledger(
                root,
                {
                    "AER": {
                        "issue_ids": ["aer-116-1"],
                        "issue_years": {"aer-116-1": 2026},
                        "authority": "official_archive",
                        "refreshed_at": "2026-09-02T00:00:00+00:00",
                    }
                },
            )
            rec = next(r for r in payload["journals"] if r["journalKey"] == "AER")
            self.assertEqual("SOURCE_BLOCKED", rec["status"])
            self.assertEqual(0, rec["publicationReadyIssueCount"])
            self.assertEqual(0, rec["missingIssueCount"])
            self.assertEqual(1, rec["sourceBlockedIssueCount"])

    def test_mismatched_archive_identity_is_not_a_ready_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"
            api.mkdir(parents=True)
            # archive says journal_id=jpe, issue_id wrong -> identity mismatch
            (api / "aer-116-1.json").write_text(
                json.dumps(archive("jpe", "aer-116-1", "ready")), encoding="utf-8"
            )
            payload = _ledger(
                root,
                {
                    "AER": {
                        "issue_ids": ["aer-116-1"],
                        "issue_years": {"aer-116-1": 2026},
                        "authority": "official_archive",
                        "refreshed_at": "2026-09-02T00:00:00+00:00",
                    }
                },
            )
            rec = next(r for r in payload["journals"] if r["journalKey"] == "AER")
            self.assertEqual(0, rec["publicationReadyIssueCount"])
            self.assertNotEqual("COMPLETE", rec["status"])
            self.assertEqual("SOURCE_BLOCKED", rec["status"])

    def test_mixed_missing_and_blocked_is_partial_with_both_lists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"
            api.mkdir(parents=True)
            (api / "aer-116-1.json").write_text(
                json.dumps(archive("aer", "aer-116-1", "source_pending")), encoding="utf-8"
            )
            payload = _ledger(
                root,
                {
                    "AER": {
                        "issue_ids": ["aer-116-1", "aer-116-2"],
                        "issue_years": {"aer-116-1": 2026, "aer-116-2": 2026},
                        "issue_refs": {
                            "aer-116-1": {"volume": "116", "issue": "1"},
                            "aer-116-2": {"volume": "116", "issue": "2"},
                        },
                        "authority": "official_archive",
                        "refreshed_at": "2026-09-02T00:00:00+00:00",
                    }
                },
            )
            rec = next(r for r in payload["journals"] if r["journalKey"] == "AER")
            self.assertEqual("PARTIAL", rec["status"])
            self.assertEqual(["aer-116-2"], [m["issue_id"] for m in rec["missingIssues"]])
            self.assertEqual(["aer-116-1"], rec["sourceBlockedIssues"])

    def test_inspect_archive_missing_vs_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = inspect_archive(root / "nope.json", expected_issue_id="x", expected_journal_id="y")
            self.assertFalse(missing["archive_exists"])
            present = root / "ok.json"
            present.write_text(json.dumps(archive("y", "x", "ready")), encoding="utf-8")
            ok = inspect_archive(present, expected_issue_id="x", expected_journal_id="y")
            self.assertTrue(ok["archive_exists"])
            self.assertEqual("ready", ok["publication_state"])


if __name__ == "__main__":
    unittest.main()
