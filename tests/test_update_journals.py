from __future__ import annotations

import copy
import unittest

from scripts.update_journals import (
    SourceLagError,
    apply_translation_cache,
    archive_issue,
    archived_issues,
    collect_one,
    collector_for,
    fallback_collector_for,
    is_detected_snapshot,
    is_publishable_snapshot,
    issue_is_newer,
    order_verification_status,
    write_archive_index,
    write_search_indexes,
)


COMPLETE_ISSUE = {
    "expected_article_count": 2,
    "research_article_count": 2,
    "articles": [
        {"doi": "10.1/a", "article_type": "research-article", "abstract_en": "A."},
        {"doi": "10.1/b", "article_type": "research-article", "abstract_en": "B."},
    ],
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
                "abstract_en": "A complete abstract.",
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
    def test_newer_detected_issue_wins_over_stale_candidate(self) -> None:
        newer = archive_fixture("demo-260-c", "260")
        newer["publication_date"] = "August 2026"
        older = archive_fixture("demo-259-c", "259")
        older["publication_date"] = "July 2026"
        self.assertTrue(issue_is_newer(newer, older))
        self.assertFalse(issue_is_newer(older, newer))
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
        for article in issue["articles"]:
            article["abstract_en"] = ""
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
            patch("scripts.update_journals.is_detected_snapshot", return_value=True),
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

    def test_incomplete_primary_abstract_is_detected_without_fallback(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        previous = {"issue_id": "demo-1-1", "articles": []}
        candidate = archive_fixture("demo-2-1", "2")
        candidate["articles"][0]["abstract_en"] = ""
        candidate["quality"]["abstract_en_complete"] = 0
        candidate["quality"]["translation_complete"] = 0
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
            patch("scripts.update_journals.fallback_collector_for") as fallback,
            patch(
                "scripts.update_journals.apply_translation_cache",
                side_effect=lambda issue: issue,
            ),
            patch(
                "scripts.update_journals.normalize_issue_content",
                side_effect=lambda issue: issue,
            ),
            patch("scripts.update_journals.validate_issue"),
            patch("scripts.update_journals.write_detected_snapshot") as detected,
        ):
            issue, report = collect_one("DEMO", config, translate=False)
        self.assertEqual(previous, issue)
        self.assertEqual("preserved_previous", report["result"])
        self.assertIn("publication gate", report["error"])
        fallback.assert_not_called()
        self.assertEqual(2, detected.call_count)

    def test_self_heal_keeps_current_without_false_progress(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        previous = {"issue_id": "demo-1-1", "articles": []}
        detected = archive_fixture("demo-2-1", "2")
        detected["publication_state"] = "enriching"
        detected["articles"][0]["abstract_en"] = ""
        detected["quality"]["abstract_en_complete"] = 0
        detected["quality"]["translation_complete"] = 0
        config = {
            "id": "demo",
            "name": "Demo Journal",
            "collector": "elsevier",
            "issn": "0000-0000",
            "current_issue_url": "https://example.org/issues",
            "issue_url_template": "https://example.org/vol/{volume}/suppl/{issue}",
        }
        with tempfile.TemporaryDirectory() as directory:
            current_path = Path(directory) / "current.json"
            detected_path = Path(directory) / "detected.json"

            def read(path: Path):
                return copy.deepcopy(detected if path == detected_path else previous)

            with (
                patch(
                    "scripts.update_journals.public_issue_path",
                    return_value=current_path,
                ),
                patch(
                    "scripts.update_journals.detected_issue_path",
                    return_value=detected_path,
                ),
                patch("scripts.update_journals.read_json", side_effect=read),
                patch(
                    "scripts.update_journals.enrich_detected_issue",
                    return_value=copy.deepcopy(detected),
                ) as enrich,
                patch(
                    "scripts.update_journals.apply_translation_cache",
                    side_effect=lambda issue: issue,
                ),
                patch(
                    "scripts.update_journals.normalize_issue_content",
                    side_effect=lambda issue: issue,
                ),
                patch("scripts.update_journals.validate_issue"),
                patch("scripts.update_journals.write_detected_snapshot"),
            ):
                issue, report = collect_one(
                    "DEMO",
                    config,
                    translate=False,
                    enrich_detected=True,
                )
        self.assertEqual(previous, issue)
        self.assertEqual("self_heal_no_change", report["result"])
        self.assertEqual(0, report["abstracts"])
        enrich.assert_called_once()

    def test_self_heal_reports_newly_recovered_abstract(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        previous = {"issue_id": "demo-1-1", "articles": []}
        detected = archive_fixture("demo-2-1", "2")
        detected["publication_state"] = "enriching"
        detected["articles"][0]["abstract_en"] = ""
        detected["quality"]["abstract_en_complete"] = 0
        detected["quality"]["translation_complete"] = 0
        progressed = copy.deepcopy(detected)
        progressed["articles"][0]["abstract_en"] = "Recovered abstract."
        progressed["quality"]["abstract_en_complete"] = 1
        config = {
            "id": "demo",
            "name": "Demo Journal",
            "collector": "elsevier",
            "issn": "0000-0000",
            "current_issue_url": "https://example.org/issues",
            "issue_url_template": "https://example.org/vol/{volume}/suppl/{issue}",
        }
        with tempfile.TemporaryDirectory() as directory:
            current_path = Path(directory) / "current.json"
            detected_path = Path(directory) / "detected.json"

            def read(path: Path):
                return copy.deepcopy(detected if path == detected_path else previous)

            with (
                patch(
                    "scripts.update_journals.public_issue_path",
                    return_value=current_path,
                ),
                patch(
                    "scripts.update_journals.detected_issue_path",
                    return_value=detected_path,
                ),
                patch("scripts.update_journals.read_json", side_effect=read),
                patch(
                    "scripts.update_journals.enrich_detected_issue",
                    return_value=progressed,
                ),
                patch(
                    "scripts.update_journals.apply_translation_cache",
                    side_effect=lambda issue: issue,
                ),
                patch(
                    "scripts.update_journals.normalize_issue_content",
                    side_effect=lambda issue: issue,
                ),
                patch("scripts.update_journals.validate_issue"),
                patch("scripts.update_journals.write_detected_snapshot"),
            ):
                issue, report = collect_one(
                    "DEMO",
                    config,
                    translate=False,
                    enrich_detected=True,
                )
        self.assertEqual(previous, issue)
        self.assertEqual("detected_progress", report["result"])
        self.assertEqual(1, report["abstracts"])

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


    def test_stale_translation_cache_is_not_applied_to_revised_source(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from scripts.translate_issue import _source_hash

        old_article = {
            "title_en": "Policy effects in 2025",
            "abstract_en": "The original 2025 abstract reports one policy effect.",
        }
        stale_translation = {
            "title_cn": "2025年政策效应",
            "abstract_cn": "原始2025年摘要报告了一项政策效应，并完整说明研究背景与主要结论。",
            "source_hash": _source_hash(old_article),
            "translation": {"provider": "test"},
        }
        issue = {
            "journal_id": "demo",
            "research_article_count": 1,
            "articles": [
                {
                    "doi": "10.1/demo",
                    "article_type": "research-article",
                    "title_en": "Policy effects in 2026",
                    "title_cn": stale_translation["title_cn"],
                    "abstract_en": "The revised 2026 abstract reports two policy effects.",
                    "abstract_cn": stale_translation["abstract_cn"],
                    "quality_flags": [],
                    "translation": {"status": "complete"},
                }
            ],
            "quality": {"flags": [], "translation_complete": 1},
        }

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "demo.json"
            cache_path.write_text(
                json.dumps({"10.1/demo": stale_translation}, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch("scripts.update_journals.TRANSLATION_CACHE", Path(directory)):
                result = apply_translation_cache(issue)

        article = result["articles"][0]
        self.assertEqual("", article["title_cn"])
        self.assertEqual("", article["abstract_cn"])
        self.assertEqual("missing", article["translation"]["status"])
        self.assertEqual(0, result["quality"]["translation_complete"])
        self.assertIn("translation_incomplete", result["quality"]["flags"])

if __name__ == "__main__":
    unittest.main()
