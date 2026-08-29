from __future__ import annotations

import unittest

from scripts.check_incremental_audit import compare_audit_logs


class IncrementalAuditTests(unittest.TestCase):
    def test_identical_failures_are_preserved(self) -> None:
        result = compare_audit_logs(
            ["FAIL ajae:10.1111/ajae.1: Translation changed numeric values: added 1"],
            ["FAIL ajae:10.1111/ajae.1: Translation changed numeric values: added 1"],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(1, len(result["preserved_baseline_failures"]))

    def test_changed_reason_and_identity_are_blocked(self) -> None:
        result = compare_audit_logs(
            ["FAIL ajae:10.1111/ajae.1: Translation changed numeric values: added 1"],
            ["FAIL ajae:10.1111/ajae.1: Translation changed numeric values: added 2", "FAIL ajae:10.1111/ajae.2: Translation changed numeric values: added 1"],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(1, len(result["worsened_failures"]))
        self.assertEqual(1, len(result["new_failures"]))

