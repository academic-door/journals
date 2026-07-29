from __future__ import annotations

import copy
import unittest

from scripts.update_journals import (
    SourceLagError,
    archive_issue,
    archived_issues,
    collect_one,
    collector_for,
    fallback_collector_for,
    is_detected_snapshot,
    is_publishable_snapshot,
    order_verification_status,
    write_archive_index,
    write_search_indexes,
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
    def test_official_issue_collector_precedes_rss_metadata_fallback(self) -> None:
        from unittest.mock import patch

        config = {
            "id": "jpe",
            "name": "Journal of Political Economy",
            "collector": "chicago",
            "issn": "0022-3808",
            "current_issue_url": "https://www.journals.uchicago.edu/toc/jpe/current",
            "rss_url": "https://www.journals.uchicago.edu/feed",
            "fallback": "crossref-repec",
        }
        with patch(
            "collectors.chicago.fetch_current_issue",
            return_value={"source": "official-issue-page"},
        ) as official:
            result = collector_for(config)()
        self.assertEqual("official-issue-page", result["source"])
        official.assert_called_once()
        self.assertIsNotNone(fallback_collector_for(config))

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

    def test_detected_gate_accepts_complete_roster_before_abstracts(self) -> None:
        issue = copy.deepcopy(COMPLETE_ISSUE)
        issue["quality"]["abstract_en_complete"] = 0
        issue["quality"]["translation_complete"] = 0
        self.assertTrue(is_detected_snapshot(issue))
        self.assertFalse(is_publishable_snapshot(issue))

    def test_detected_gate_rejects_untrusted_roster(self) -> None:
        issue = copy.deepcopy(COMPLETE_ISSUE)
        issue["quality"]["order_preserved"] = False
        self.assertFalse(is_detected_snapshot(issue))

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

    def test_partial_translation_does_not_replace_previous_snapshot(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        previous = {"issue_id": "demo-1-1", "articles": []}
        candidate = archive_fixture("demo-2-1", "2")
        candidate["research_article_count"] = 2
        candidate["expected_article_count"] = 2
        candidate["articles"].append(
            {
                "doi": "10.1/second",
                "article_type": "research-article",
            }
        )
        candidate["quality"].update(
            {
                "doi_complete": 2,
                "authors_complete": 2,
                "abstract_en_complete": 2,
                "translation_complete": 1,
            }
        )
        config = {
            "id": "demo",
            "current_issue_url": "https://example.org/current",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "scripts.update_journals.public_issue_path",
                return_value=Path(directory) / "current.json",
            ),
            patch("scripts.update_journals.read_json", return_value=previous),
            patch(
                "scripts.update_journals.collector_for",
                return_value=lambda: copy.deepcopy(candidate),
            ),
            patch("scripts.update_journals.apply_translation_cache", side_effect=lambda issue: issue),
            patch("scripts.update_journals.normalize_issue_content", side_effect=lambda issue: issue),
            patch("scripts.update_journals.validate_issue"),
            patch("scripts.update_journals.is_publishable_snapshot", return_value=True),
            patch("scripts.update_journals.write_detected_snapshot") as write_detected,
            patch("scripts.update_journals.write_json") as write_json,
        ):
            issue, report = collect_one("DEMO", config, translate=False)
        self.assertEqual(previous, issue)
        self.assertEqual("preserved_previous", report["result"])
        self.assertIn("translation incomplete: 1/2", report["error"])
        write_json.assert_not_called()
        self.assertEqual(2, write_detected.call_count)

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

    def test_search_indexes_cover_latest_and_history_without_page_embedding(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        old_issue = archive_fixture("demo-1-1", "1")
        new_issue = archive_fixture("demo-2-1", "2")
        for issue, title in (
            (old_issue, "Old China Study"),
            (new_issue, "New Study"),
        ):
            issue["publication_date"] = f"202{issue['volume']}-01"
            issue["articles"][0].update(
                {
                    "paper_id": f"paper-{issue['volume']}",
                    "sequence": 1,
                    "title_en": title,
                    "title_cn": "测试论文",
                    "authors": ["Test Author"],
                    "abstract_en": "Evidence from China." if issue is old_issue else "Abstract",
                    "abstract_cn": "摘要",
                    "source_url": "https://example.org/paper",
                }
            )
        config = {
            "DEMO": {
                "id": "demo",
                "short_name": "DEMO",
                "name": "Demo Journal",
                "field": "general",
                "tier": "A",
                "enabled": True,
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_issue(old_issue, api_root=root)
            archive_issue(new_issue, api_root=root)
            write_search_indexes(
                config,
                {"DEMO": new_issue},
                updated_at="2026-07-28T00:00:00+00:00",
                api_root=root,
            )
            latest = json.loads((root / "search" / "latest.json").read_text(encoding="utf-8"))
            history = json.loads((root / "search" / "all.json").read_text(encoding="utf-8"))
            manifest = json.loads((root / "search" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(1, latest["record_count"])
        self.assertEqual(2, history["record_count"])
        self.assertEqual(2, manifest["issue_count"])
        old_record = next(record for record in history["records"] if record["issue_id"] == "demo-1-1")
        self.assertEqual("1", old_record["volume"])
        self.assertEqual("1", old_record["issue"])
        self.assertTrue(old_record["china_related"])


if __name__ == "__main__":
    unittest.main()
