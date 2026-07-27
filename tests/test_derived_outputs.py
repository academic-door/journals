from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DerivedOutputsTest(unittest.TestCase):
    def test_continuous_publication_volumes_are_not_reported_as_pending(self):
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        from update_journals import is_continuous_publication, order_verification_status

        continuous = {
            "issue": "C",
            "quality": {"flags": ["official_order_unverified"]},
        }
        numbered = {
            "issue": "3",
            "quality": {"flags": ["official_order_unverified"]},
        }
        verified = {"issue": "3", "quality": {"flags": []}}
        self.assertTrue(is_continuous_publication(continuous))
        self.assertFalse(is_continuous_publication(numbered))
        self.assertEqual("continuous_publication", order_verification_status(continuous))
        self.assertEqual("pending_official", order_verification_status(numbered))
        self.assertEqual("official_verified", order_verification_status(verified))

    def test_non_research_rule_is_shared_and_keeps_scholarly_comments(self):
        from collectors import article_types

        self.assertTrue(article_types.is_non_research("Front Matter"))
        self.assertFalse(article_types.is_non_research("Comment on: market power"))
        self.assertEqual("comment", article_types.article_type("Comment on: market power"))
        self.assertEqual("comment", article_types.article_type("Discussion of: credit limits"))
        self.assertEqual(
            "comment", article_types.article_type("Introduction to the Special Issue")
        )
        self.assertEqual(
            "research-article",
            article_types.article_type("A discussion of welfare in general equilibrium"),
        )
        pipeline = (ROOT / "scripts" / "update_journals.py").read_text(encoding="utf-8")
        self.assertIn("article_types.article_type", pipeline)
        self.assertIn("article_type_breakdown", pipeline)

    def test_feed_and_search_index_shapes(self):
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        import outputs

        issue = {
            "journal_id": "aer",
            "journal_name": "American Economic Review",
            "issue_id": "aer-116-7",
            "volume": "116",
            "issue": "7",
            "issue_label": "Vol. 116 · No. 7",
            "retrieved_at": "2026-07-27T00:00:00+00:00",
            "research_article_count": 1,
            "quality": {"translation_complete": 1},
            "articles": [
                {
                    "paper_id": "doi:10.1257/aer.1",
                    "title_en": "A Title",
                    "title_cn": "一个标题",
                    "authors": ["Someone"],
                    "abstract_cn": "摘要",
                    "doi": "10.1257/aer.1",
                    "source_url": "https://doi.org/10.1257/aer.1",
                }
            ],
        }
        feed = outputs.build_feed("t", "d", "/api/v1/feeds/all.xml", [issue], issue["retrieved_at"])
        self.assertIn("<rss version=\"2.0\"", feed)
        self.assertIn("一个标题", feed)
        self.assertIn("<guid isPermaLink=\"false\">doi:10.1257/aer.1</guid>", feed)

        index = outputs.build_search_index([issue], {"aer": {"short_name": "AER", "collection": "top5"}}, "now")
        self.assertEqual(1, index["record_count"])
        self.assertEqual("top5", index["records"][0]["collection"])

        archive = outputs.build_archive_index("aer", "American Economic Review", [issue], "now")
        self.assertEqual(1, archive["issue_count"])
        self.assertEqual("aer-116-7", archive["issues"][0]["issue_id"])

    def test_search_page_and_navigation_exist(self):
        page = (ROOT / "src/pages/search/index.astro").read_text(encoding="utf-8")
        layout = (ROOT / "src/layouts/Layout.astro").read_text(encoding="utf-8")
        self.assertIn("api/v1/search-index.json", page)
        self.assertIn("跨刊检索", layout)

    def test_field_explorer_reuses_shared_design_tokens(self):
        explorer = (ROOT / "src/components/FieldJournalExplorer.astro").read_text(encoding="utf-8")
        self.assertIn("official_verified", explorer)
        self.assertIn("continuous_publication", explorer)
        self.assertIn("issue-note verification", explorer)
        self.assertNotIn('order_verification === "verified"', explorer)
        self.assertNotIn("#8a5b14", explorer)


if __name__ == "__main__":
    unittest.main()
