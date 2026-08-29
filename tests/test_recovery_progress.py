from __future__ import annotations

import unittest

from scripts.check_recovery_progress import evaluate_progress


def record(issue_id: str, category: str, **extra):
    return {"issue_id": issue_id, "category": category, "counts": {}, **extra}


class RecoveryProgressTests(unittest.TestCase):
    def test_accepts_new_ready_issue(self) -> None:
        result = evaluate_progress(
            {"records": [record("aer-1-1", "recoverable")]},
            {"records": [record("aer-1-1", "ready", archive_exists=True)]},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(["aer-1-1"], result["new_ready_issue_ids"])

    def test_rejects_no_progress(self) -> None:
        before = {"records": [record("aer-1-1", "source_pending")]}
        result = evaluate_progress(before, before)
        self.assertFalse(result["ok"])
        self.assertIn("wave produced no measurable progress", result["errors"])

    def test_rejects_ready_regression(self) -> None:
        result = evaluate_progress(
            {"records": [record("aer-1-1", "ready")]},
            {"records": [record("aer-1-1", "source_pending")]},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(["aer-1-1"], result["lost_ready_issue_ids"])

    def test_accepts_missing_archive_becoming_explicit_source_pending(self) -> None:
        result = evaluate_progress(
            {"records": [record("aer-1-1", "recoverable", archive_exists=False)]},
            {"records": [record("aer-1-1", "source_pending", archive_exists=True)]},
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["warnings"])

    def test_accepts_source_pending_reduction_without_ready_increment(self) -> None:
        result = evaluate_progress(
            {"records": [record("aer-1-1", "source_pending")]},
            {"records": [record("aer-1-1", "recoverable")]},
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["source_pending_reduced"])


if __name__ == "__main__":
    unittest.main()
