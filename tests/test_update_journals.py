from __future__ import annotations

import copy
import unittest

from scripts.update_journals import (
    SourceLagError,
    archive_issue,
    archived_issues,
    collect_one,
    is_publishable_snapshot,
    order_verification_status,
    write_archive_index,
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


def archive_fixture(issue_id: str, volume: str) -> dict:
    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": "demo",
        "journal_name": "Demo Journal",
        "volume": volume,
        "issue": "1",
        "source_url": "https://example.org/current",
        "retrieved_at": f"2026-0{volume}-01T00:00:00+00:00",
        "expected_article_count": 1,
        "research_article_count": 1,
        "status": "ready",
        "articles": [
            {
                "doi": f"10.1/{volume}",
                "article_type": "research-article",
            }
        ],
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "doi_complete": 1,
            "authors_complete": 1,
            "abstract_en_complete": 1,
            "translation_complete": 1,
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

    def test_archive_preserves_old_issue_and_builds_index(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        old_issue = archive_fixture("demo-1-1", "1")
        new_issue = archive_fixture("demo-2-1", "2")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_path = archive_issue(old_issue, api_root=root)
            new_path = archive_issue(new_issue, api_root=root)
            self.assertTrue(old_path and old_path.exists())
            self.assertTrue(new_path and new_path.exists())
            write_archive_index(
                "demo",
                "Demo Journal",
                updated_at="2026-07-28T00:00:00+00:00",
                api_root=root,
            )
            index = json.loads(
                (root / "journals" / "demo" / "issues" / "index.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(2, index["issue_count"])
        self.assertEqual(["demo-2-1", "demo-1-1"], [item["issue_id"] for item in index["issues"]])

    def test_archive_is_immutable_and_rejects_unsafe_ids(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        issue = archive_fixture("demo-1-1", "1")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = archive_issue(issue, api_root=root)
            changed = copy.deepcopy(issue)
            changed["publication_date"] = "changed"
            archive_issue(changed, api_root=root)
            stored = json.loads(path.read_text(encoding="utf-8"))
            unsafe = copy.deepcopy(issue)
            unsafe["issue_id"] = "../escape"
            rejected = archive_issue(unsafe, api_root=root)
        self.assertNotIn("publication_date", stored)
        self.assertIsNone(rejected)

    def test_untranslated_snapshot_is_not_archived(self) -> None:
        import tempfile
        from pathlib import Path

        issue = archive_fixture("demo-1-1", "1")
        issue["quality"]["translation_complete"] = 0
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(archive_issue(issue, api_root=Path(directory)))


if __name__ == "__main__":
    unittest.main()
