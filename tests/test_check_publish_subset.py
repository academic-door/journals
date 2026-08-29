from __future__ import annotations

import unittest

from scripts.check_publish_subset import build_report


def status(ready: int, missing: int, source_pending: int) -> dict:
    return {
        "summary": {"complete": ready, "pending": source_pending},
        "coverage": {
            "publication_ready": ready,
            "missing": missing,
            "source_pending": source_pending,
        },
    }


def gap(*ready: str) -> dict:
    return {"records": [{"issue_id": value, "category": "ready"} for value in ready]}


class PublishSubsetTests(unittest.TestCase):
    def test_a_mode_accepts_only_named_new_ready_issues(self) -> None:
        report = build_report(
            status(1046, 87, 198),
            status(1048, 85, 198),
            gap("old-ready"),
            gap("old-ready", "ere-85-1", "ere-87-6"),
            ["ere-85-1", "ere-87-6"],
            0,
        )
        self.assertTrue(report["ok"])
        self.assertEqual(["ere-85-1", "ere-87-6"], report["publishable_issue_ids"])
        self.assertEqual([], report["regressed_ready_issue_ids"])
        self.assertEqual(0, report["translation_model_calls"])

    def test_existing_ready_regression_fails(self) -> None:
        report = build_report(
            status(1046, 87, 198),
            status(1046, 87, 198),
            gap("old-ready"),
            gap("ere-85-1"),
            ["ere-85-1"],
            0,
        )
        self.assertFalse(report["ok"])
        self.assertEqual(["old-ready"], report["regressed_ready_issue_ids"])

