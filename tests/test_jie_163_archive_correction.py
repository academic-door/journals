from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.update_journals import (
    ROOT,
    archive_issue,
    archived_issues,
    stamp_issue_readiness,
    write_archive_index,
    write_json,
)


EVIDENCE_PATH = ROOT / "data" / "provenance" / "official-rosters" / "jie" / "jie-163-c.json"
OFFICIAL_ORDER = (
    "10.1016/j.jinteco.2025.104203",
    "10.1016/j.jinteco.2025.104207",
    "10.1016/j.jinteco.2025.104204",
    "10.1016/j.jinteco.2026.104219",
    "10.1016/j.jinteco.2026.104225",
    "10.1016/j.jinteco.2026.104231",
    "10.1016/j.jinteco.2026.104230",
    "10.1016/j.jinteco.2026.104290",
)
LEGACY_ORDER = (
    "10.1016/j.jinteco.2025.104203",
    "10.1016/j.jinteco.2025.104204",
    "10.1016/j.jinteco.2025.104207",
    "10.1016/j.jinteco.2026.104219",
    "10.1016/j.jinteco.2026.104225",
    "10.1016/j.jinteco.2026.104230",
    "10.1016/j.jinteco.2026.104231",
    "10.1016/j.jinteco.2026.104290",
)


def official_candidate() -> dict:
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    articles = []
    for index, item in enumerate(sorted(evidence["items"], key=lambda row: row["sequence"]), 1):
        articles.append(
            {
                "paper_id": f"doi:{item['doi']}",
                "sequence": item["sequence"],
                "source_sequence": item["source_sequence"],
                "article_type": "research-article",
                "title_en": item["title_en"],
                "title_cn": f"测试标题 {index}",
                "authors": ["Test Author"],
                "abstract_en": f"A complete synthetic abstract for JIE article {index}.",
                "abstract_cn": f"这是用于归档纠偏测试的完整合成摘要 {index}。",
                "doi": item["doi"],
                "source_url": item["official_article_url"],
                "translation": {
                    "status": "complete",
                    "provider": "fixture",
                    "source_hash": f"fixture-hash-{index}",
                },
                "quality_flags": [],
            }
        )
    quality = {
        "roster_match": True,
        "order_preserved": True,
        "roster_transport": "official-page-read",
        "roster_authority": "official-issue-page",
        "roster_match_scope": "official-publisher-issue-full",
        "official_item_count": 10,
        "publishable_item_count": 8,
        "excluded_item_count": 2,
        "excluded_items": copy.deepcopy(evidence["excluded_items"]),
        "doi_complete": 8,
        "authors_complete": 8,
        "abstract_en_complete": 8,
        "translation_complete": 8,
        "duplicate_count": 0,
        "flags": [
            "publisher_html_blocked_sciencedirect_rss_fallback",
            "publisher_rss_reverse_order_normalized",
        ],
        "content_counts": {
            "official_items": 10,
            "observed_items": 10,
            "publishable_items": 8,
            "research_articles": 8,
            "editorial_material": 2,
        },
        "official_roster_evidence": {
            "captured_at": evidence["captured_at"],
            "official_url": evidence["official_url"],
            "method": evidence["method"],
            "item_count": 8,
            "excluded_item_count": 2,
            "archive_reordered": False,
            "privacy_fields_stored": False,
        },
        "publisher_page_status": "first-party-observed",
    }
    candidate = {
        "schema_version": "1.0",
        "issue_id": "jie-163-c",
        "journal_id": "jie",
        "journal_name": "Journal of International Economics",
        "volume": "163",
        "issue": "C",
        "issue_label": "Vol. 163",
        "publication_date": "October 2026",
        "source_url": evidence["official_url"],
        "retrieved_at": "2026-10-21T08:49:43+00:00",
        "expected_article_count": 8,
        "research_article_count": 8,
        "status": "incomplete",
        "articles": articles,
        "quality": quality,
    }
    stamp_issue_readiness(candidate)
    return candidate


def legacy_ready_archive(candidate: dict) -> dict:
    archive = copy.deepcopy(candidate)
    by_doi = {
        article["doi"].casefold(): article for article in archive["articles"]
    }
    archive["articles"] = [by_doi[doi.casefold()] for doi in LEGACY_ORDER]
    for index, article in enumerate(archive["articles"], 1):
        article["sequence"] = index
        article["source_sequence"] = index
    quality = archive["quality"]
    quality["roster_transport"] = "repec-serial-page"
    quality["roster_authority"] = "repec-publisher-supplied"
    quality["roster_match_scope"] = "repec-volume-items"
    quality["official_item_count"] = 9
    quality["excluded_item_count"] = 1
    quality["excluded_items"] = [copy.deepcopy(quality["excluded_items"][1])]
    quality["content_counts"].update(
        official_items=9,
        observed_items=9,
        editorial_material=1,
    )
    quality.pop("official_roster_evidence")
    quality.pop("publisher_page_status")
    archive["retrieved_at"] = "2026-08-05T10:02:37+00:00"
    archive["status"] = "ready"
    archive["quality"] = quality
    archive["source_status"] = "publisher_verified"
    return archive


def other_jie_history_issue(volume: int) -> dict:
    return {
        "schema_version": "1.0",
        "issue_id": f"jie-{volume}-c",
        "journal_id": "jie",
        "journal_name": "Journal of International Economics",
        "volume": str(volume),
        "issue": "C",
        "publication_date": "August 2025",
        "source_url": "https://example.org/jie",
        "retrieved_at": "2025-08-01T00:00:00+00:00",
        "expected_article_count": 1,
        "research_article_count": 1,
        "status": "ready",
        "articles": [
            {
                "doi": f"10.5555/jie.{volume}",
                "article_type": "research-article",
                "title_en": "Historical article",
                "title_cn": "历史文章",
                "authors": ["Test Author"],
                "abstract_en": "A complete historical abstract.",
                "abstract_cn": "完整历史摘要。",
                "translation": {"status": "complete"},
            }
        ],
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_authority": "official-issue-page",
            "roster_transport": "official-issue-page",
            "doi_complete": 1,
            "authors_complete": 1,
            "abstract_en_complete": 1,
            "translation_complete": 1,
            "duplicate_count": 0,
            "flags": [],
        },
    }


class Jie163ReadyArchiveCorrectionTests(unittest.TestCase):
    def test_ready_current_alone_can_coexist_with_stale_ready_archive(self) -> None:
        candidate = official_candidate()
        stale = legacy_ready_archive(candidate)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_path = root / "journals" / "jie" / "issues" / "current.json"
            archive_path = root / "journals" / "jie" / "issues" / "jie-163-c.json"
            write_json(current_path, candidate)
            write_json(archive_path, stale)

            current = json.loads(current_path.read_text(encoding="utf-8"))
            archive_issue(copy.deepcopy(current), api_root=root)
            preserved = json.loads(archive_path.read_text(encoding="utf-8"))

        self.assertEqual("ready", current["publication_state"])
        self.assertEqual("official_verified", current["source_status"])
        self.assertEqual("incomplete", current["status"])
        self.assertEqual(OFFICIAL_ORDER, tuple(article["doi"] for article in current["articles"]))
        self.assertEqual(LEGACY_ORDER, tuple(article["doi"] for article in preserved["articles"]))
        self.assertEqual(1, len(preserved["quality"]["excluded_items"]))

    def test_scoped_correction_updates_archive_and_index_without_touching_history(self) -> None:
        candidate = official_candidate()
        stale = legacy_ready_archive(candidate)
        stale["first_seen_at"] = "2026-08-05T10:02:37+00:00"
        stale["observed_at"] = "2026-08-05T10:02:37+00:00"
        for article in stale["articles"]:
            article["first_seen_at"] = "2026-08-05T10:02:37+00:00"
            article["observed_at"] = "2026-08-05T10:02:37+00:00"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "journals" / "jie" / "issues" / "jie-163-c.json"
            write_json(archive_path, stale)
            for volume in range(140, 163):
                archive_issue(other_jie_history_issue(volume), api_root=root)

            before_other = {
                path.name: path.read_bytes()
                for path in (root / "journals" / "jie" / "issues").glob("jie-*-c.json")
                if path.name != "jie-163-c.json"
            }
            corrected_path = archive_issue(
                copy.deepcopy(candidate),
                api_root=root,
                correct_jie_163_ready_archive=True,
            )
            corrected = json.loads(corrected_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-08-05T10:02:37+00:00", corrected["first_seen_at"])
            self.assertEqual("2026-08-05T10:02:37+00:00", corrected["observed_at"])
            self.assertTrue(
                all(
                    article["observed_at"] == "2026-08-05T10:02:37+00:00"
                    for article in corrected["articles"]
                )
            )
            self.assertEqual(OFFICIAL_ORDER, tuple(article["doi"] for article in corrected["articles"]))
            self.assertEqual(2, len(corrected["quality"]["excluded_items"]))
            corrected_by_doi = {
                article["doi"]: article for article in corrected["articles"]
            }
            stale_by_doi = {article["doi"]: article for article in stale["articles"]}
            for doi in OFFICIAL_ORDER:
                self.assertEqual(
                    stale_by_doi[doi]["translation"],
                    corrected_by_doi[doi]["translation"],
                )
                self.assertEqual(stale_by_doi[doi]["title_cn"], corrected_by_doi[doi]["title_cn"])
                self.assertEqual(
                    stale_by_doi[doi]["abstract_cn"],
                    corrected_by_doi[doi]["abstract_cn"],
                )

            index_before = root / "journals" / "jie" / "issues" / "index.json"
            self.assertFalse(index_before.exists())
            write_archive_index(
                "jie",
                "Journal of International Economics",
                updated_at="2026-10-22T00:00:00+00:00",
                api_root=root,
            )
            index = json.loads(index_before.read_text(encoding="utf-8"))
            index_entry = next(item for item in index["issues"] if item["issue_id"] == "jie-163-c")
            after_other = {
                path.name: path.read_bytes()
                for path in (root / "journals" / "jie" / "issues").glob("jie-*-c.json")
                if path.name != "jie-163-c.json"
            }

            archive_issue(
                copy.deepcopy(candidate),
                api_root=root,
                correct_jie_163_ready_archive=True,
            )
            repeated = json.loads(archive_path.read_text(encoding="utf-8"))

        self.assertEqual(24, index["issue_count"])
        self.assertEqual("official_verified", index_entry["source_status"])
        self.assertEqual("ready", index_entry["publication_state"])
        self.assertEqual(8, index_entry["article_count"])
        self.assertEqual(before_other, after_other)
        self.assertEqual(corrected["retrieved_at"], repeated["retrieved_at"])

    def test_invalid_official_candidate_cannot_replace_ready_archive(self) -> None:
        candidate = official_candidate()
        stale = legacy_ready_archive(candidate)
        bad = copy.deepcopy(candidate)
        bad["quality"]["excluded_items"] = bad["quality"]["excluded_items"][1:]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "journals" / "jie" / "issues" / "jie-163-c.json"
            write_json(archive_path, stale)
            with self.assertRaises(ValueError):
                archive_issue(
                    bad,
                    api_root=root,
                    correct_jie_163_ready_archive=True,
                )
            preserved = json.loads(archive_path.read_text(encoding="utf-8"))
        self.assertEqual(LEGACY_ORDER, tuple(article["doi"] for article in preserved["articles"]))
        self.assertEqual(1, len(preserved["quality"]["excluded_items"]))

    def test_explicit_correction_flag_cannot_rewrite_other_ready_issue(self) -> None:
        original = other_jie_history_issue(162)
        changed = copy.deepcopy(original)
        changed["articles"][0]["title_en"] = "A changed title"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = archive_issue(original, api_root=root)
            with self.assertRaises(ValueError):
                archive_issue(
                    changed,
                    api_root=root,
                    correct_jie_163_ready_archive=True,
                )
            preserved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("Historical article", preserved["articles"][0]["title_en"])


if __name__ == "__main__":
    unittest.main()
