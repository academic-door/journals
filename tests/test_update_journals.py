from __future__ import annotations

import copy
import unittest

from scripts.update_journals import (
    SourceLagError,
    collect_one,
    is_publishable_snapshot,
    order_verification_status,
)


COMPLETE_ISSUE = {
    "expected_article_count": 2,
    "research_article_count": 2,
    "articles": [{"doi": "10.1/a"}, {"doi": "10.1/b"}],
    "quality": {
        "roster_match": True,
        "order_preserved": True,
        "doi_complete": 2,
        "authors_complete": 2,
        "abstract_en_complete": 2,
        "duplicate_count": 0,
        "flags": [],
    },
}


class PublicationGateTests(unittest.TestCase):
    def test_accepts_complete_snapshot(self) -> None:
        self.assertTrue(is_publishable_snapshot(COMPLETE_ISSUE))

    def test_rejects_self_declared_incomplete_roster(self) -> None:
        issue = copy.deepcopy(COMPLETE_ISSUE)
        issue["expected_article_count"] = 3
        self.assertFalse(is_publishable_snapshot(issue))

    def test_rejects_missing_metadata_and_duplicates(self) -> None:
        issue = copy.deepcopy(COMPLETE_ISSUE)
        issue["quality"]["abstract_en_complete"] = 1
        issue["quality"]["duplicate_count"] = 1
        self.assertFalse(is_publishable_snapshot(issue))

    def test_exposes_reader_facing_order_verification_level(self) -> None:
        issue = copy.deepcopy(COMPLETE_ISSUE)
        self.assertEqual("official_verified", order_verification_status(issue))
        issue["quality"]["flags"] = ["official_order_override_applied"]
        self.assertEqual("official_verified", order_verification_status(issue))
        issue["quality"]["flags"] = ["official_order_unverified"]
        self.assertEqual("pending_official", order_verification_status(issue))
        issue["quality"]["flags"] = ["crossref_provisional_roster"]
        self.assertEqual("pending_official", order_verification_status(issue))

    def test_source_lag_does_not_replace_previous_snapshot(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        previous = {"issue_id": "demo-10", "articles": []}
        config = {
            "id": "demo",
            "current_issue_url": "https://example.org/current",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("scripts.update_journals.public_issue_path", return_value=Path(directory) / "current.json"),
            patch("scripts.update_journals.read_json", return_value=previous),
            patch(
                "scripts.update_journals.collector_for",
                return_value=lambda: {"volume": "10", "issue": "1"},
            ),
            patch("scripts.update_journals.is_publishable_snapshot", return_value=True),
        ):
            issue, report = collect_one(
                "DEMO",
                config,
                translate=False,
                expected_volume="11",
            )
        self.assertEqual(previous, issue)
        self.assertEqual("preserved_previous", report["result"])
        self.assertIn(SourceLagError.__name__, report["error"])


if __name__ == "__main__":
    unittest.main()
