from __future__ import annotations

import copy
import unittest

from collectors.article_types import (
    canonical_article_type,
    normalize_issue_taxonomy,
)


class ArticleTypeTests(unittest.TestCase):
    def test_canonical_types_cover_publishable_and_excluded_material(self) -> None:
        self.assertEqual("correction", canonical_article_type("Corrigendum to a paper"))
        self.assertEqual("editorial", canonical_article_type("Editorial Board"))
        self.assertEqual("front-matter", canonical_article_type("Front Matter"))
        self.assertEqual("comment", canonical_article_type("Comment on a paper"))
        self.assertEqual(
            "short-communication",
            canonical_article_type("A paper", raw_type="Short communication"),
        )

    def test_issue_counts_and_progress_require_source_abstracts(self) -> None:
        issue = {
            "expected_article_count": 3,
            "research_article_count": 3,
            "articles": [
                {
                    "doi": "10.1/research",
                    "article_type": "research-article",
                    "title_en": "Research paper",
                    "title_cn": "研究论文",
                    "abstract_en": "English abstract.",
                    "abstract_cn": "中文摘要。",
                },
                {
                    "doi": "10.1/short",
                    "article_type": "research-article",
                    "title_en": "Short paper",
                    "title_cn": "短文",
                    "abstract_en": "",
                    "abstract_cn": "旧中文摘要不应算完成。",
                },
                {
                    "doi": "10.1/comment",
                    "article_type": "comment",
                    "title_en": "Comment on a paper",
                    "title_cn": "评论",
                    "abstract_en": "",
                    "abstract_cn": "",
                },
            ],
            "quality": {
                "official_item_count": 6,
                "excluded_items": [
                    {"title_en": "Corrigendum to a paper"},
                    {"title_en": "Editorial Board"},
                    {"title_en": "Front Matter"},
                ],
                "flags": [],
            },
        }
        normalized = normalize_issue_taxonomy(
            copy.deepcopy(issue),
            overrides={"10.1/short": "short-communication"},
        )
        counts = normalized["content_counts"]
        self.assertEqual(6, counts["official_items"])
        self.assertEqual(3, counts["publishable_items"])
        self.assertEqual(1, counts["research_articles"])
        self.assertEqual(1, counts["short_communications"])
        self.assertEqual(1, counts["comments"])
        self.assertEqual(1, counts["corrections"])
        self.assertEqual(1, counts["editorial_material"])
        self.assertEqual(1, counts["front_matter"])
        self.assertEqual(2, normalized["quality"]["abstract_en_complete"])
        self.assertEqual(2, normalized["quality"]["translation_complete"])
        self.assertIn("abstract_en_incomplete", normalized["quality"]["flags"])
        self.assertIn("translation_incomplete", normalized["quality"]["flags"])


if __name__ == "__main__":
    unittest.main()
