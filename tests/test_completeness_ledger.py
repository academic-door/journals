"""Tests for the 2026 expected-vs-observed completeness ledger builder."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_completeness_ledger import build_ledger, render_markdown


JOURNALS = {
    "AER": {"id": "aer", "name": "American Economic Review"},
    "JPE": {"id": "jpe", "name": "Journal of Political Economy"},
    "QJE": {"id": "qje", "name": "The Quarterly Journal of Economics"},
    "EJ": {"id": "ej", "name": "The Economic Journal"},
}


def _archive(journal_id: str, issue_id: str, status: str = "ready") -> dict:
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


class CompletenessLedgerTests(unittest.TestCase):
    def test_status_classifications(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api_root = root / "api"
            # --- AER: 2 expected 2026 issues, 1 archived => PARTIAL ---
            (api_root / "journals" / "aer" / "issues").mkdir(parents=True)
            (api_root / "journals" / "aer" / "issues" / "aer-128-1.json").write_text(
                json.dumps(_archive("aer", "aer-128-1")), encoding="utf-8"
            )
            # --- EJ: 1 expected 2026 issue, archived => COMPLETE ---
            (api_root / "journals" / "ej" / "issues").mkdir(parents=True)
            (api_root / "journals" / "ej" / "issues" / "ej-130-1.json").write_text(
                json.dumps(_archive("ej", "ej-130-1")), encoding="utf-8"
            )

            state_path = root / "field-2025-2026.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "issues": {
                            "aer-128-1": {"journal": "AER", "year": 2026, "volume": "128", "issue": "1", "status": "ready"},
                            "aer-128-2": {"journal": "AER", "year": 2026, "volume": "128", "issue": "2", "status": "pending"},
                            "jpe-132-1": {"journal": "JPE", "year": 2026, "volume": "132", "issue": "1", "status": "pending"},
                            "ej-130-1": {"journal": "EJ", "year": 2026, "volume": "130", "issue": "1", "status": "ready"},
                        },
                        "discovery": {
                            "AER": {
                                "issue_ids": ["aer-128-1", "aer-128-2"],
                                "issue_years": {"aer-128-1": 2026, "aer-128-2": 2026},
                                "authority": "official_archive",
                                "refreshed_at": "2026-09-02T00:00:00+00:00",
                            },
                            "JPE": {
                                "issue_ids": ["jpe-132-1"],
                                "issue_years": {"jpe-132-1": 2026},
                                "authority": "captcha_blocked",
                                "refreshed_at": "2026-09-02T00:00:00+00:00",
                            },
                            "EJ": {
                                "issue_ids": ["ej-130-1"],
                                "issue_years": {"ej-130-1": 2026},
                                "authority": "official_archive",
                                "refreshed_at": "2026-09-02T00:00:00+00:00",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            payload = build_ledger(
                state_paths=[state_path],
                journals=JOURNALS,
                api_root=api_root,
            )
            by_key = {r["journalKey"]: r for r in payload["journals"]}

            self.assertEqual("1.0", payload["schema_version"])
            self.assertEqual(4, payload["reconciliation"]["journal_count"])

            aer = by_key["AER"]
            self.assertEqual("PARTIAL", aer["status"])
            self.assertEqual(2, aer["expectedIssueCount"])
            self.assertEqual(1, aer["observedIssueCount"])
            self.assertEqual(1, aer["missingIssueCount"])
            self.assertEqual(["aer-128-2"], [m["issue_id"] for m in aer["missingIssues"]])

            ej = by_key["EJ"]
            self.assertEqual("COMPLETE", ej["status"])
            self.assertEqual(1, ej["expectedIssueCount"])
            self.assertEqual(0, ej["missingIssueCount"])

            jpe = by_key["JPE"]
            self.assertEqual("SOURCE_BLOCKED", jpe["status"])
            self.assertEqual(1, jpe["expectedIssueCount"])
            self.assertEqual(0, jpe["observedIssueCount"])

            qje = by_key["QJE"]
            self.assertEqual("NOT_MEASURED", qje["status"])
            self.assertEqual(0, qje["expectedIssueCount"])

    def test_deterministic_order_and_missing_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "field-2025-2026.json"
            state_path.write_text(
                json.dumps(
                    {
                        "discovery": {
                            "AER": {
                                "issue_ids": ["aer-128-2", "aer-128-1"],
                                "issue_years": {"aer-128-2": 2026, "aer-128-1": 2026},
                                "authority": "official_archive",
                            }
                        },
                        "issues": {},
                    }
                ),
                encoding="utf-8",
            )
            payload = build_ledger(
                state_paths=[state_path], journals=JOURNALS, api_root=root / "api"
            )
            aer = next(r for r in payload["journals"] if r["journalKey"] == "AER")
            self.assertEqual(["aer-128-1", "aer-128-2"], aer["expectedIssues"])
            self.assertEqual(["aer-128-1", "aer-128-2"], [m["issue_id"] for m in aer["missingIssues"]])


if __name__ == "__main__":
    unittest.main()
