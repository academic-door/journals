from __future__ import annotations

import copy
import unittest

from scripts import update_journals


def issue(issue_id: str, *, source_status: str, publication_state: str) -> dict:
    payload = {
        "issue_id": issue_id,
        "journal_id": "ecta",
        "volume": "94",
        "issue": "4",
        "publication_date": "July 2026",
        "expected_article_count": 1,
        "research_article_count": 1,
        "articles": [
            {
                "doi": "10.3982/ecta-test",
                "title_en": "Test article",
                "authors": ["Author"],
                "abstract_en": "A complete abstract for the readiness fixture.",
                "title_cn": "测试文章",
                "abstract_cn": "这是一个完整的测试摘要，用于验证通过当前语义数值检查的归档版本可以安全作为当前快照。",
                "article_type": "research-article",
            }
        ],
        "content_status": "complete",
        "source_status": source_status,
        "publication_state": publication_state,
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "doi_complete": 1,
            "authors_complete": 1,
            "abstract_en_complete": 1,
            "translation_complete": 1,
            "duplicate_count": 0,
            "roster_authority": (
                "repec-publisher-supplied"
                if publication_state == "ready"
                else "crossref-provisional"
            ),
            "roster_transport": (
                "repec-serial-page"
                if publication_state == "ready"
                else "crossref"
            ),
            "flags": [] if publication_state == "ready" else ["crossref_provisional_roster"],
        },
    }
    return payload


class ReadyArchiveFloorTests(unittest.TestCase):
    def test_same_issue_ready_archive_beats_regressed_current(self) -> None:
        current = issue(
            "ecta-94-4",
            source_status="source_pending",
            publication_state="source_pending",
        )
        archive = issue(
            "ecta-94-4",
            source_status="publisher_verified",
            publication_state="ready",
        )

        chosen = update_journals.prefer_ready_archive(current, archive)

        self.assertEqual("ready", chosen["publication_state"])
        self.assertEqual("publisher_verified", chosen["source_status"])
        self.assertEqual(
            "repec-publisher-supplied",
            chosen["quality"]["roster_authority"],
        )

    def test_same_issue_authority_lift_does_not_restore_bad_archive_translation(self) -> None:
        current = issue("landecon-102-3", source_status="source_pending", publication_state="source_pending")
        current["marker"] = "current"
        current["articles"][0]["abstract_cn"] = (
            "研究结果显示，披露该建议带来的年度福利收益为345万美元，"
            "当前修正版内容必须保留。"
        )
        archive = issue("landecon-102-3", source_status="official_verified", publication_state="ready")
        archive["quality"]["roster_authority"] = "official-issue-page"
        archive["quality"]["roster_transport"] = "official-page-read"
        archive["quality"]["official_roster_evidence"] = {
            "official_url": "https://example.org/official/102/3",
            "item_count": 1,
        }
        article = archive["articles"][0]
        article["abstract_en"] = "The annual welfare benefit is $3.45 million from the disclosed advisory."
        article["abstract_cn"] = (
            "研究结果显示，披露该建议带来的年度福利收益为3.45万美元，"
            "这一旧归档数量级错误不得覆盖当前内容。"
        )

        chosen = update_journals.prefer_ready_archive(current, archive)

        self.assertEqual("current", chosen["marker"])
        self.assertEqual(
            current["articles"][0]["abstract_cn"],
            chosen["articles"][0]["abstract_cn"],
        )
        self.assertEqual("official_verified", chosen["source_status"])
        self.assertEqual("ready", chosen["publication_state"])
        self.assertEqual(
            "official-issue-page", chosen["quality"]["roster_authority"]
        )

    def test_same_issue_authority_is_not_lifted_when_ordered_doi_roster_differs(self) -> None:
        current = issue("landecon-102-3", source_status="source_pending", publication_state="source_pending")
        current["marker"] = "current"
        archive = issue("landecon-102-3", source_status="official_verified", publication_state="ready")
        archive["articles"][0]["doi"] = "10.3982/different"

        chosen = update_journals.prefer_ready_archive(current, archive)

        self.assertEqual("current", chosen["marker"])
        self.assertEqual("source_pending", chosen["publication_state"])

    def test_same_issue_ready_current_is_not_replaced(self) -> None:
        current = issue(
            "ecta-94-4",
            source_status="publisher_verified",
            publication_state="ready",
        )
        current["marker"] = "current"
        archive = copy.deepcopy(current)
        archive["marker"] = "archive"

        chosen = update_journals.prefer_ready_archive(current, archive)

        self.assertEqual("current", chosen["marker"])


if __name__ == "__main__":
    unittest.main()
