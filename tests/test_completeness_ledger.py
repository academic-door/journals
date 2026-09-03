"""Tests for the r2 2026 completeness ledger engine (exclusions + authority)."""

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


def state(discovery: dict, exclusions: dict | None = None, issues: dict | None = None) -> dict:
    return {"schema_version": "1.1", "issues": issues or {}, "discovery": discovery,
            "expected_issue_exclusions": exclusions or {}}


def archive(journal_id: str, issue_id: str, status: str = "ready") -> dict:
    return {"schema_version": "1.0", "issue_id": issue_id, "journal_id": journal_id,
            "volume": "1", "issue": "1", "status": status, "articles": [], "quality": {}}


def _ledger(tmp: Path, discovery: dict, exclusions: dict | None = None, window_end: str = "") -> dict:
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps(state(discovery, exclusions)), encoding="utf-8")
    return build_ledger(state_paths=[state_path], journals=JOURNALS, api_root=tmp / "api", window_end=window_end)


def _rec(payload: dict, key: str) -> dict:
    return next(r for r in payload["journals"] if r["journalKey"] == key)


def authoritative_snap(issue_ids, years, authority="official_archive", refs=None):
    return {
        "issue_ids": issue_ids,
        "issue_years": {i: y for i, y in zip(issue_ids, years)},
        "issue_refs": refs or {},
        "authority": authority,
        "refreshed_at": "2026-09-02T00:00:00+00:00",
    }


class CompletenessLedgerR2Tests(unittest.TestCase):
    def test_excluded_not_yet_published_is_not_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"; api.mkdir(parents=True)
            (api / "aer-116-1.json").write_text(json.dumps(archive("aer", "aer-116-1")), encoding="utf-8")
            payload = _ledger(root, {"AER": authoritative_snap(["aer-116-1", "aer-116-2"], [2026, 2026],
                                                               refs={"aer-116-1": {"volume": "116", "issue": "1"}})},
                              exclusions={"aer-116-2": {"status": "not_yet_published", "reason": "not published"}})
            rec = _rec(payload, "AER")
            self.assertIn("aer-116-2", [e["issue_id"] for e in rec["excludedIssues"]])
            self.assertEqual([], rec["missingIssues"])
            self.assertEqual(1, rec["expectedIssueCount"])  # B
            self.assertEqual(["aer-116-1"], rec["effectiveExpectedIssues"])

    def test_exclusion_alone_does_not_manufacture_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = _ledger(root, {"AER": authoritative_snap(["aer-116-1"], [2026])},
                              exclusions={"aer-116-1": {"status": "not_yet_published"}})
            rec = _rec(payload, "AER")
            self.assertEqual("NOT_MEASURED", rec["status"])  # C
            self.assertEqual(0, rec["expectedIssueCount"])

    def test_unknown_authority_is_not_measured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = _ledger(root, {"AER": authoritative_snap(["aer-116-1"], [2026], authority="mystery_source")})
            rec = _rec(payload, "AER")
            self.assertEqual("NOT_MEASURED", rec["status"])  # D
            self.assertEqual("unknown", rec["authorityClassification"])

    def test_crossref_candidate_is_not_measured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = _ledger(root, {"AER": authoritative_snap(["aer-116-1"], [2026], authority="crossref_candidate")})
            rec = _rec(payload, "AER")
            self.assertEqual("NOT_MEASURED", rec["status"])  # E

    def test_verified_roster_all_ready_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"; api.mkdir(parents=True)
            (api / "aer-116-1.json").write_text(json.dumps(archive("aer", "aer-116-1")), encoding="utf-8")
            payload = _ledger(root, {"AER": authoritative_snap(["aer-116-1"], [2026])})
            rec = _rec(payload, "AER")
            self.assertEqual("COMPLETE", rec["status"])  # F
            self.assertEqual(1, rec["publicationReadyIssueCount"])

    def test_verified_roster_issue_absent_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = _ledger(root, {"AER": authoritative_snap(["aer-116-1"], [2026])})
            rec = _rec(payload, "AER")
            self.assertEqual("PARTIAL", rec["status"])  # G
            self.assertEqual(["aer-116-1"], [m["issue_id"] for m in rec["missingIssues"]])

    def test_inspect_archive_missing_vs_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(inspect_archive(root / "nope.json", expected_issue_id="x", expected_journal_id="y")["archive_exists"])
            present = root / "ok.json"
            present.write_text(json.dumps(archive("y", "x", "ready")), encoding="utf-8")
            self.assertEqual("ready", inspect_archive(present, expected_issue_id="x", expected_journal_id="y")["publication_state"])


class CompletenessLedgerFreshnessTests(unittest.TestCase):
    def _complete_ledger(self, window_end: str):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"; api.mkdir(parents=True)
            (api / "aer-116-1.json").write_text(json.dumps(archive("aer", "aer-116-1")), encoding="utf-8")
            payload = _ledger(root, {"AER": authoritative_snap(["aer-116-1"], [2026])}, window_end=window_end)
            return payload

    def test_evidence_at_audit_end_is_current(self) -> None:
        # refreshed_at == window end -> CURRENT_FOR_AUDIT_END
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"; api.mkdir(parents=True)
            (api / "aer-116-1.json").write_text(json.dumps(archive("aer", "aer-116-1")), encoding="utf-8")
            snap = authoritative_snap(["aer-116-1"], [2026])
            snap["refreshed_at"] = "2026-09-02T00:00:00+00:00"
            state_path = root / "state.json"
            state_path.write_text(json.dumps(state({"AER": snap})), encoding="utf-8")
            payload = build_ledger(state_paths=[state_path], journals=JOURNALS, api_root=root / "api", window_end="2026-09-02")
            rec = _rec(payload, "AER")
            self.assertEqual("COMPLETE", rec["status"])
            self.assertEqual("CURRENT_FOR_AUDIT_END", rec["freshnessStatus"])
            self.assertEqual(1, payload["reconciliation"]["complete_current_to_audit_end"])

    def test_evidence_older_than_audit_end_is_stale(self) -> None:
        payload = self._complete_ledger("2026-09-02")  # refreshed_at default 2026-09-02? set below explicitly
        # authoritative_snap default refreshed_at is 2026-09-02; force older
        rec = _rec(payload, "AER")
        # Use a controlled state for 08-24 evidence
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "journals" / "aer" / "issues"; api.mkdir(parents=True)
            (api / "aer-116-1.json").write_text(json.dumps(archive("aer", "aer-116-1")), encoding="utf-8")
            snap = authoritative_snap(["aer-116-1"], [2026])
            snap["refreshed_at"] = "2026-08-24T00:00:00+00:00"
            state_path = root / "state.json"
            state_path.write_text(json.dumps(state({"AER": snap})), encoding="utf-8")
            payload = build_ledger(state_paths=[state_path], journals=JOURNALS, api_root=root / "api", window_end="2026-09-02")
            rec = _rec(payload, "AER")
            self.assertEqual("STALE_FOR_AUDIT_END", rec["freshnessStatus"])
            self.assertEqual(0, payload["reconciliation"]["complete_current_to_audit_end"])
            self.assertEqual(1, payload["reconciliation"]["complete_as_measured"])



if __name__ == "__main__":
    unittest.main()
