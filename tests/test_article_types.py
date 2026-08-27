from __future__ import annotations

import copy
import unittest

from collectors.article_types import (
    canonical_article_type,
    canonical_issue_label,
    normalize_issue_taxonomy,
)


class ArticleTypeTests(unittest.TestCase):
    def test_issue_labels_distinguish_numbers_parts_and_url_tokens(self) -> None:
        self.assertEqual("Vol. 143", canonical_issue_label("143", "C"))
        self.assertEqual(
            "Vol. 183",
            canonical_issue_label("183", "C", "Vol. 183 · No. C"),
        )
        self.assertEqual("Vol. 256 · Part A", canonical_issue_label("256", "A"))
        self.assertEqual("Vol. 141 · No. 3", canonical_issue_label("141", "3"))

    def test_canonical_types_cover_publishable_and_excluded_material(self) -> None:
        self.assertEqual("correction", canonical_article_type("Corrigendum to a paper"))
        self.assertEqual("editorial", canonical_article_type("Editorial Board"))
        self.assertEqual("front-matter", canonical_article_type("Front Matter"))
        self.assertEqual("comment", canonical_article_type("Comment on a paper"))
        self.assertEqual(
            "comment",
            canonical_article_type(
                "Tax Smoothing in Frictional Labor Markets: A Reply"
            ),
        )
        self.assertEqual(
            "comment",
            canonical_article_type("Tax Smoothing in Frictional Labor Markets: A Comment"),
        )
        self.assertEqual("front-matter", canonical_article_type("First Page"))
        self.assertEqual("front-matter", canonical_article_type("ANNOUNCEMENTS"))
        self.assertEqual(
            "front-matter",
            canonical_article_type(
                "Preliminary Program AFA 2023 ANNUAL MEETING AMERICAN FINANCE ASSOCIATION"
            ),
        )
        self.assertEqual(
            "front-matter",
            canonical_article_type(
                "Participant Schedule for the AFA 2023 Preliminary Program January 6-8, 2023"
            ),
        )
        self.assertEqual(
            "front-matter", canonical_article_type("AMERICAN FINANCE ASSOCIATION")
        )
        self.assertEqual("front-matter", canonical_article_type("Index to Volume 131"))
        self.assertEqual(
            "research-article",
            canonical_article_type(
                "Front-Page News: The Effect of News Positioning on Financial Markets"
            ),
        )
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



    def test_front_matter_and_editorial_families_classify_correctly(self) -> None:
        from collectors.article_types import canonical_article_type

        cases = {
            "AAEA fellows": "front-matter",
            "AAEA Fellows": "front-matter",
            "Award winning theses": "front-matter",
            "Presidents": "editorial",
            "Editor's Note": "editorial",
            "Editors' Note": "editorial",
            "Editors’ Report for 2024": "editorial",
            "Editor’s Report for 2025": "editorial",
            "Introduction by the Editor": "editorial",
            "Introduction by Editors": "editorial",
            "Themed issue: Quantile regression and data heterogeneity": "editorial",
            "2025 JAERE Excellence in Refereeing Award": "editorial",
            "Report of the EST and of the 2025 Annual Membership Meeting": "editorial",
            "Retraction of: Mortgage Finance and Climate Change": "correction",
            "Expression of Concern: Man versus Machine Learning": "correction",
        }
        for title, expected in cases.items():
            self.assertEqual(
                expected,
                canonical_article_type(title, "research-article"),
                title,
            )

    def test_person_name_without_abstract_is_excluded_as_front_matter(self) -> None:
        import copy

        from collectors.article_types import normalize_issue_taxonomy

        issue = {
            "expected_article_count": 2,
            "research_article_count": 2,
            "articles": [
                {
                    "paper_id": "doi:10.1/real",
                    "doi": "10.1/real",
                    "title_en": "Research paper",
                    "title_cn": "研究论文",
                    "abstract_en": "A complete abstract.",
                    "abstract_cn": "完整摘要。",
                    "article_type": "research-article",
                    "authors": ["A"],
                },
                {
                    "paper_id": "doi:10.1/memorial",
                    "doi": "10.1/memorial",
                    "title_en": "Titus Awokuse",
                    "title_cn": "",
                    "abstract_en": "",
                    "abstract_cn": "",
                    "article_type": "research-article",
                    "authors": ["Jesse Tack"],
                },
            ],
            "quality": {"excluded_items": [], "flags": []},
        }
        normalized = normalize_issue_taxonomy(copy.deepcopy(issue))
        self.assertEqual(1, normalized["content_counts"]["publishable_items"])
        self.assertEqual(1, normalized["research_article_count"])
        self.assertEqual(1, len(normalized["articles"]))
        self.assertEqual("10.1/real", normalized["articles"][0]["doi"])
        self.assertEqual(
            "front_matter",
            normalized["quality"]["excluded_items"][0]["reason"],
        )



if __name__ == "__main__":
    unittest.main()
