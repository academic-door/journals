from __future__ import annotations

import unittest

from scripts.classify_closeout_manifest import classify


class CloseoutManifestTests(unittest.TestCase):
    def test_categories_are_mutually_exclusive_and_sum_to_public_unresolved(self) -> None:
        public = {
            "updated_at": "2026-08-29T00:00:00+00:00",
            "summary": {"complete": 10},
            "coverage": {
                "missing": 1,
                "source_pending": 5,
                "missing_issue_ids": ["a", "b"],
                "source_pending_issue_ids": ["c", "d", "e", "f"],
            },
        }
        candidate = {
            "records": [
                {"issue_id": "a", "category": "ready"},
                {"issue_id": "b", "category": "translation_required"},
                {"issue_id": "c", "category": "recoverable"},
                {"issue_id": "d", "category": "source_pending", "collector": "oup", "retry_class": "source"},
                {"issue_id": "e", "category": "source_pending", "collector": "chicago", "retry_class": "manual", "reason": "HTTPError: 403 Client Error"},
                {"issue_id": "f", "category": "source_pending", "collector": "crossref", "retry_class": "source"},
            ]
        }
        result = classify(public, candidate)
        self.assertEqual(
            {
                "READY_CANDIDATE": 1,
                "TRANSLATION_FIX": 1,
                "SOURCE_RECOVERABLE": 2,
                "BROWSER_REQUIRED": 1,
                "EXTERNAL_BLOCKED": 1,
                "PIPELINE_BLOCKED": 0,
            },
            result["counts"],
        )
        self.assertEqual(6, sum(result["counts"].values()))

    def test_verified_a_translation_blocker_is_reclassified_to_translation_fix(self) -> None:
        public = {
            "summary": {"complete": 2},
            "coverage": {
                "missing": 2,
                "source_pending": 0,
                "missing_issue_ids": ["ere-85-1", "ere-87-6"],
                "source_pending_issue_ids": [],
            },
        }
        candidate = {
            "records": [
                {"issue_id": "ere-85-1", "category": "recoverable"},
                {"issue_id": "ere-87-6", "category": "recoverable"},
            ]
        }
        result = classify(
            public,
            candidate,
            force_translation_fix_issue_ids={"ere-85-1", "ere-87-6"},
        )
        self.assertEqual(0, result["counts"]["READY_CANDIDATE"])
        self.assertEqual(2, result["counts"]["TRANSLATION_FIX"])
        self.assertEqual(
            "A-class closeout verified translation_partial; route to translation-only recovery",
            result["details"]["ere-85-1"]["reclassification_reason"],
        )


if __name__ == "__main__":
    unittest.main()
