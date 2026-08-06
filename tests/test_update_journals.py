from __future__ import annotations

import copy
import json
import unittest
from unittest import mock

from scripts.update_journals import (
    SourceLagError,
    apply_translation_cache,
    archive_issue,
    archived_issues,
    collect_one,
    clean_abstract_label,
    collector_for,
    fallback_collector_for,
    is_detected_snapshot,
    is_publishable_snapshot,
    issue_is_newer,
    merge_issue_audit_metadata,
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
    def test_placeholder_is_not_a_complete_abstract(self) -> None:
        self.assertEqual("", clean_abstract_label("Please provide abstract."))

    def test_crossref_provisional_roster_cannot_be_promoted(self) -> None:
        issue = copy.deepcopy(COMPLETE_ISSUE)
        issue["quality"]["flags"] = ["crossref_provisional_roster"]
        self.assertFalse(is_detected_snapshot(issue))
        # A previously published last-known-good snapshot must remain usable
        # in public indexes; collect_one blocks new provisional promotion.
        self.assertTrue(is_publishable_snapshot(issue))

    def test_new_crossref_provisional_roster_preserves_previous(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        previous = archive_fixture("demo-1-1", "1")
        provisional = archive_fixture("demo-2-1", "2")
        provisional["quality"]["flags"] = ["crossref_provisional_roster"]
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
                return_value=lambda: copy.deepcopy(provisional),
            ),
            patch("scripts.update_journals.is_detected_snapshot", return_value=True),
        ):
            issue, report = collect_one("DEMO", config, translate=False)
        self.assertEqual(previous, issue)
        self.assertEqual("preserved_previous", report["result"])
        self.assertIn("requires official confirmation", report["error"])

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

    def test_failed_official_rss_can_reach_provisional_crossref_fallback(self) -> None:
        from unittest.mock import patch

        config = {
            "id": "ajae",
            "name": "American Journal of Agricultural Economics",
            "collector": "wiley",
            "issn": "1467-8276",
            "current_issue_url": "https://onlinelibrary.wiley.com/toc/14678276/current",
            "rss_url": "https://onlinelibrary.wiley.com/feed/14678276/most-recent",
            "fallback": "crossref",
        }
        provisional = {"quality": {"flags": ["crossref_provisional_roster"]}}
        with (
            patch(
                "collectors.metadata_fallback.fetch_official_rss_issue",
                side_effect=RuntimeError("official RSS blocked"),
            ) as official_rss,
            patch(
                "collectors.metadata_fallback.fetch_crossref_current_issue",
                return_value=provisional,
            ) as crossref,
        ):
            result = fallback_collector_for(config)()
        self.assertEqual(provisional, result)
        official_rss.assert_called_once()
        crossref.assert_called_once()

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

    def test_translation_only_self_heal_skips_publisher_enrichment(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        previous = {"issue_id": "demo-1-1", "articles": []}
        detected = archive_fixture("demo-2-1", "2")
        detected["publication_state"] = "enriching"
        detected["quality"]["translation_complete"] = 0
        detected["articles"][0]["title_cn"] = ""
        detected["articles"][0]["abstract_cn"] = ""
        config = {
            "id": "demo",
            "name": "Demo Journal",
            "collector": "elsevier",
            "issn": "0000-0000",
            "current_issue_url": "https://example.org/issues",
        }
        with tempfile.TemporaryDirectory() as directory:
            current_path = Path(directory) / "current.json"
            detected_path = Path(directory) / "detected.json"

            def read(path: Path):
                return copy.deepcopy(detected if path == detected_path else previous)

            with (
                patch("scripts.update_journals.public_issue_path", return_value=current_path),
                patch("scripts.update_journals.detected_issue_path", return_value=detected_path),
                patch("scripts.update_journals.read_json", side_effect=read),
                patch("scripts.update_journals.enrich_detected_issue") as enrich,
                patch("scripts.update_journals.apply_translation_cache", side_effect=lambda issue: issue),
                patch("scripts.update_journals.normalize_issue_content", side_effect=lambda issue: issue),
                patch("scripts.update_journals.validate_issue"),
                patch("scripts.update_journals.write_detected_snapshot"),
            ):
                _issue, report = collect_one(
                    "DEMO", config, translate=False, enrich_detected=True
                )
        enrich.assert_not_called()
        self.assertEqual("translation-only", report["transport"])

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

    def test_archive_index_sorts_real_months_and_restores_issue_numbers(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        fixtures = []
        for issue_id, volume, number, publication_date in (
            ("demo-93-6", "93", "6", "November 2025"),
            ("demo-94-3", "94", "3", "May 2026"),
            ("demo-94-4", "94", "4", "July 2026"),
        ):
            issue = archive_fixture(issue_id, volume)
            issue["issue"] = number
            issue["publication_date"] = publication_date
            issue["issue_label"] = f"Vol. {volume}"
            fixtures.append(issue)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for issue in fixtures:
                archive_issue(issue, api_root=root)
            write_archive_index(
                "demo",
                "Demo Journal",
                updated_at="2026-07-31T00:00:00+00:00",
                api_root=root,
            )
            index = json.loads(
                (root / "journals" / "demo" / "issues" / "index.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(
            ["demo-94-4", "demo-94-3", "demo-93-6"],
            [item["issue_id"] for item in index["issues"]],
        )
        self.assertEqual(
            ["Vol. 94 · No. 4", "Vol. 94 · No. 3", "Vol. 93 · No. 6"],
            [item["issue_label"] for item in index["issues"]],
        )
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

    def test_incomplete_refresh_updates_only_safe_issue_audit_metadata(self) -> None:
        previous = {
            "issue_id": "demo-1-c",
            "articles": [{"doi": "10.1/demo", "abstract_cn": "已审核译文"}],
            "content_counts": {"publishable_items": 1, "corrections": 0},
            "quality": {
                "content_counts": {"publishable_items": 1, "corrections": 0},
                "excluded_items": [],
                "official_item_count": 1,
            },
        }
        candidate = {
            "issue_id": "demo-1-c",
            "articles": [{"doi": "10.1/demo", "abstract_cn": ""}],
            "content_counts": {
                "publishable_items": 1,
                "corrections": 1,
                "editorial_material": 1,
            },
            "quality": {
                "content_counts": {
                    "publishable_items": 1,
                    "corrections": 1,
                    "editorial_material": 1,
                },
                "excluded_items": [
                    {"article_type": "correction", "title_en": "Corrigendum"},
                    {"article_type": "editorial", "title_en": "Editorial Board"},
                ],
                "official_item_count": 3,
            },
        }

        result = merge_issue_audit_metadata(previous, candidate)

        self.assertEqual("已审核译文", result["articles"][0]["abstract_cn"])
        self.assertEqual(1, result["content_counts"]["corrections"])
        self.assertEqual(1, result["quality"]["content_counts"]["corrections"])
        self.assertEqual(2, len(result["quality"]["excluded_items"]))
        self.assertEqual(3, result["quality"]["official_item_count"])


    def test_refresh_elsevier_abstracts_replaces_fallback_but_keeps_roster(self) -> None:
        from unittest.mock import patch

        from scripts.update_journals import refresh_elsevier_abstracts

        issue = {
            "issue_id": "demo-188-c",
            "articles": [
                {
                    "doi": "10.1016/j.demo.2026.010",
                    "title_en": "Existing paper",
                    "abstract_en": "OpenAlex fallback abstract.",
                    "source_url": "https://www.sciencedirect.com/science/article/pii/S0100000000000001",
                    "sources": {"abstract_en": "openalex"},
                }
            ],
            "quality": {"abstract_en_complete": 1, "flags": []},
        }
        config = {"collector": "elsevier", "id": "demo"}
        with patch(
            "collectors.metadata_fallback._elsevier_lookup"
        ) as lookup:
            lookup.return_value = {
                "abstract": "Publisher supplied abstract.",
                "teaser": "",
                "source": "elsevier-article-metadata",
                "status": "success_full_abstract",
                "attempts": [],
                "rate_limit": {"limit": 20000, "remaining": 19500},
            }
            result = refresh_elsevier_abstracts(issue, config)
        article = result["articles"][0]
        self.assertEqual("Publisher supplied abstract.", article["abstract_en"])
        self.assertEqual(
            "elsevier-article-metadata", article["sources"]["abstract_en"]
        )
        self.assertEqual(
            {"limit": 20000, "remaining": 19500},
            article["sources"]["abstract_lookup"]["rate_limit"],
        )
        self.assertEqual("demo-188-c", result["issue_id"])
        lookup.assert_called_once()

    def test_refresh_elsevier_abstracts_keeps_existing_on_failure(self) -> None:
        from unittest.mock import patch

        from scripts.update_journals import refresh_elsevier_abstracts

        issue = {
            "issue_id": "demo-188-c",
            "articles": [
                {
                    "doi": "10.1016/j.demo.2026.010",
                    "title_en": "Existing paper",
                    "abstract_en": "Previously recovered abstract.",
                    "source_url": "https://www.sciencedirect.com/science/article/pii/S0100000000000001",
                    "sources": {"abstract_en": "openalex"},
                }
            ],
            "quality": {"abstract_en_complete": 1, "flags": []},
        }
        config = {"collector": "elsevier", "id": "demo"}
        with patch(
            "collectors.metadata_fallback._elsevier_lookup"
        ) as lookup:
            lookup.return_value = {
                "abstract": "",
                "teaser": "A teaser must never pass as the abstract.",
                "source": "",
                "status": "success_teaser_only",
                "attempts": [],
            }
            result = refresh_elsevier_abstracts(issue, config)
        article = result["articles"][0]
        self.assertEqual("Previously recovered abstract.", article["abstract_en"])
        self.assertEqual("openalex", article["sources"]["abstract_en"])

    def test_refresh_elsevier_abstracts_ignores_non_elsevier(self) -> None:
        from unittest.mock import patch

        from scripts.update_journals import refresh_elsevier_abstracts

        issue = {
            "issue_id": "other-1-c",
            "articles": [
                {
                    "doi": "10.1111/j.other.2026.010",
                    "title_en": "Other",
                    "abstract_en": "Abstract.",
                    "source_url": "https://example.org/article/1",
                    "sources": {"abstract_en": "crossref"},
                }
            ],
            "quality": {"abstract_en_complete": 1, "flags": []},
        }
        config = {"collector": "wiley", "id": "other"}
        with patch("collectors.metadata_fallback._elsevier_lookup") as lookup:
            result = refresh_elsevier_abstracts(issue, config)
        self.assertEqual("Abstract.", result["articles"][0]["abstract_en"])
        lookup.assert_not_called()



class LatestIssuePreferenceTests(unittest.TestCase):
    def test_archive_newer_than_current_becomes_latest(self) -> None:
        import tempfile
        from pathlib import Path

        from scripts.update_journals import load_available_issues

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issues_dir = root / "journals" / "jeea" / "issues"
            issues_dir.mkdir(parents=True)
            current = {
                "schema_version": "1.0",
                "journal_id": "jeea",
                "journal_name": "Journal of the European Economic Association",
                "issue_id": "jeea-24-3",
                "volume": "24",
                "issue": "3",
                "issue_label": "Vol. 24 No. 3",
                "publication_date": "June 2026",
                "publication_state": "ready",
                "status": "ready",
                "research_article_count": 1,
                "articles": [
                    {
                        "paper_id": "doi:10.1/x",
                        "sequence": 1,
                        "article_type": "research",
                        "title_en": "T",
                        "title_cn": "题",
                        "authors": ["A"],
                        "abstract_en": "Abstract text that is long enough to pass validation for the publication gate.",
                        "abstract_cn": "摘要文本足够长以通过发布门槛的校验。",
                        "doi": "10.1/x",
                        "source_url": "https://doi.org/10.1/x",
                        "sources": {"roster": "test"},
                    }
                ],
                "quality": {
                    "translation_complete": 1,
                    "content_counts": {"publishable_items": 1, "observed_items": 1, "official_items": 1},
                    "roster_authority": "crossref",
                    "roster_transport": "crossref",
                },
            }
            archived = dict(current)
            archived["issue_id"] = "jeea-24-4"
            archived["volume"] = "24"
            archived["issue"] = "4"
            archived["publication_date"] = "August 2026"
            (issues_dir / "current.json").write_text(
                json.dumps(current, ensure_ascii=False), encoding="utf-8"
            )
            (issues_dir / "jeea-24-4.json").write_text(
                json.dumps(archived, ensure_ascii=False), encoding="utf-8"
            )
            configs = {
                "JEEA": {
                    "id": "jeea",
                    "name": "Journal of the European Economic Association",
                    "enabled": True,
                }
            }
            with (
                mock.patch("scripts.update_journals.PUBLIC_API", root),
                mock.patch("scripts.update_journals.normalize_issue_content", side_effect=lambda x: x),
                mock.patch("scripts.update_journals.validate_issue", return_value=None),
                mock.patch("scripts.update_journals.is_publishable_snapshot", return_value=True),
            ):
                available = load_available_issues(configs, {})
            self.assertEqual("jeea-24-4", available["JEEA"]["issue_id"])
            # current.json is refreshed to the newest archive
            refreshed = json.loads(
                (issues_dir / "current.json").read_text(encoding="utf-8")
            )
            self.assertEqual("jeea-24-4", refreshed["issue_id"])

    def test_current_newer_than_archive_stays(self) -> None:
        import tempfile
        from pathlib import Path

        from scripts.update_journals import load_available_issues

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issues_dir = root / "journals" / "jep" / "issues"
            issues_dir.mkdir(parents=True)
            current = {
                "schema_version": "1.0",
                "journal_id": "jep",
                "journal_name": "JEP",
                "issue_id": "jep-40-3",
                "volume": "40",
                "issue": "3",
                "issue_label": "Vol. 40 No. 3",
                "publication_date": "Summer 2026",
                "publication_state": "ready",
                "status": "ready",
                "research_article_count": 1,
                "articles": [
                    {
                        "paper_id": "doi:10.2/x",
                        "sequence": 1,
                        "article_type": "research",
                        "title_en": "T",
                        "title_cn": "题",
                        "authors": ["A"],
                        "abstract_en": "Abstract text that is long enough to pass validation for the publication gate.",
                        "abstract_cn": "摘要文本足够长以通过发布门槛的校验。",
                        "doi": "10.2/x",
                        "source_url": "https://doi.org/10.2/x",
                        "sources": {"roster": "test"},
                    }
                ],
                "quality": {
                    "translation_complete": 1,
                    "content_counts": {"publishable_items": 1, "observed_items": 1, "official_items": 1},
                    "roster_authority": "crossref",
                    "roster_transport": "crossref",
                },
            }
            older_archive = dict(current)
            older_archive["issue_id"] = "jep-40-2"
            older_archive["issue"] = "2"
            older_archive["publication_date"] = "May 2026"
            # Also ensure an unparseable current label ("Summer 2026") is not
            # outranked by an older parseable archive.
            current["publication_date"] = "Summer 2026"
            (issues_dir / "current.json").write_text(
                json.dumps(current, ensure_ascii=False), encoding="utf-8"
            )
            (issues_dir / "jep-40-2.json").write_text(
                json.dumps(older_archive, ensure_ascii=False), encoding="utf-8"
            )
            configs = {
                "JEP": {
                    "id": "jep",
                    "name": "JEP",
                    "enabled": True,
                }
            }
            with (
                mock.patch("scripts.update_journals.PUBLIC_API", root),
                mock.patch("scripts.update_journals.normalize_issue_content", side_effect=lambda x: x),
                mock.patch("scripts.update_journals.validate_issue", return_value=None),
            ):
                available = load_available_issues(configs, {})
            self.assertEqual("jep-40-3", available["JEP"]["issue_id"])




if __name__ == "__main__":
    unittest.main()

