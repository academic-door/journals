from __future__ import annotations

import unittest

from collectors.article_types import normalize_no_abstract_comment
from scripts.update_journals import preserve_existing_content


class NoAbstractCommentSemanticsTests(unittest.TestCase):
    def test_normalizes_title_only_comment_contract(self) -> None:
        article = {
            "article_type": "comment",
            "title_en": "Example: A Comment",
            "title_cn": "示例：评论",
            "abstract_en": "",
            "abstract_cn": "该项目没有可用的摘要。",
            "quality_flags": [],
            "translation": {"status": "complete", "provider": "google-translate"},
        }
        self.assertTrue(normalize_no_abstract_comment(article))
        self.assertEqual("", article["abstract_cn"])
        self.assertIn("abstract_en_missing", article["quality_flags"])
        self.assertNotIn("abstract_cn_missing", article["quality_flags"])
        self.assertEqual("complete", article["translation"]["status"])

    def test_research_article_is_not_relaxed(self) -> None:
        article = {
            "article_type": "research-article",
            "title_en": "Research",
            "title_cn": "研究",
            "abstract_en": "",
            "abstract_cn": "占位",
            "quality_flags": [],
            "translation": {"status": "complete"},
        }
        self.assertFalse(normalize_no_abstract_comment(article))
        self.assertEqual("占位", article["abstract_cn"])

    def test_preserve_existing_content_does_not_restore_comment_placeholder(self) -> None:
        incoming = {
            "issue_id": "jpe-133-4",
            "research_article_count": 1,
            "quality": {"flags": []},
            "articles": [{
                "doi": "10.1086/734091",
                "article_type": "comment",
                "title_en": "The Incredible Taylor Principle: A Comment",
                "title_cn": "",
                "authors": ["A"],
                "abstract_en": "",
                "abstract_cn": "",
                "quality_flags": ["title_cn_missing", "abstract_en_missing"],
                "sources": {},
                "translation": {"status": "pending"},
            }],
        }
        existing = {
            "issue_id": "jpe-133-4",
            "articles": [{
                "doi": "10.1086/734091",
                "article_type": "comment",
                "title_en": "The Incredible Taylor Principle: A Comment",
                "title_cn": "令人难以置信的泰勒原理：评论",
                "authors": ["A"],
                "abstract_en": "",
                "abstract_cn": "该项目没有可用的摘要。",
                "quality_flags": [],
                "sources": {"roster": "repec-serial-page"},
                "translation": {"status": "complete", "provider": "google-translate"},
            }],
        }
        result = preserve_existing_content(incoming, existing)
        article = result["articles"][0]
        self.assertEqual("令人难以置信的泰勒原理：评论", article["title_cn"])
        self.assertEqual("", article["abstract_cn"])
        self.assertIn("abstract_en_missing", article["quality_flags"])
        self.assertEqual("complete", article["translation"]["status"])


if __name__ == "__main__":
    unittest.main()
