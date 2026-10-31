import html
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urljoin

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import oup
from scripts.translate_issue import validate_translation
from scripts.update_journals import (
    is_detected_snapshot,
    is_publishable_snapshot,
    issue_content_status,
    issue_publication_state,
    normalize_issue_content,
)


ISSUE_URL = "https://academic.oup.com/joeg/issue/26/5"
CONFIGURED_ISSUE_URL = "https://academic.oup.com/joeg/issue"
FIXTURE = ROOT / "tests" / "fixtures" / "b8" / "joeg-26-5-official-toc.json"


def issue_page(dois, *, volume="26", issue="5"):
    cards = []
    article_pages = {}
    for sequence, doi in enumerate(dois, start=1):
        page = 650 + sequence
        article_url = f"https://academic.oup.com/joeg/article/26/5/{page}/fixture{sequence}"
        title = f"JOEG test title {sequence}"
        cards.append(
            f"""
            <div class="al-article-item-wrap">
              <h5 class="al-title-list"><a href="{article_url}">{title}</a></h5>
              <a href="https://doi.org/{doi}">{doi}</a>
            </div>
            """
        )
        article_pages[article_url] = f"""
            <html><head>
              <meta name="citation_title" content="{html.escape(title)}">
              <meta name="citation_author" content="Test Author {sequence}">
              <meta name="citation_doi" content="{html.escape(doi)}">
              <meta name="citation_abstract" content="This official fixture abstract {sequence} describes a synthetic economic geography topic for the issue-processing regression.">
            </head><body></body></html>
        """.encode("utf-8")

    page = f"""
        <html><body>
          <div class="issue-info"><h1>Volume {volume}, Issue {issue}, September 2026</h1></div>
          {''.join(cards)}
        </body></html>
    """
    return page.encode("utf-8"), article_pages


class Joeg265CollectorTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.dois = [row["doi"] for row in self.data["source_items"]]

    def test_stage_fixture_matches_adjudicated_issue_scoped_roster(self):
        expected_dois = [row[0] for row in oup.JOEG_26_5_ROSTER]
        expected_sections = [row[1] for row in oup.JOEG_26_5_ROSTER]
        self.assertEqual(expected_dois, self.dois)
        self.assertEqual(
            expected_sections,
            [row["publisher_section"] for row in self.data["source_items"]],
        )
        self.assertFalse(self.data["enabled"])

    def test_exact_issue_has_three_comments_six_research_and_audited_correction(self):
        issue_content, article_pages = issue_page(self.dois)

        def fake_get(session, url, attempts=oup.MAX_ATTEMPTS):
            del session, attempts
            if url == ISSUE_URL:
                return SimpleNamespace(content=issue_content, url=ISSUE_URL)
            return SimpleNamespace(content=article_pages[url], url=url)

        with patch.object(oup, "_get", side_effect=fake_get):
            issue = oup.fetch_current_issue(ISSUE_URL)

        self.assertEqual("joeg-26-5", issue["issue_id"])
        self.assertEqual(9, issue["expected_article_count"])
        self.assertEqual(9, issue["research_article_count"])
        self.assertEqual(
            ["comment"] * 3 + ["research-article"] * 6,
            [article["article_type"] for article in issue["articles"]],
        )
        self.assertEqual(self.dois[:9], [article["doi"] for article in issue["articles"]])
        self.assertEqual(
            {
                "official_items": 10,
                "observed_items": 10,
                "publishable_items": 9,
                "research_articles": 6,
                "comments": 3,
                "short_communications": 0,
                "corrections": 1,
                "editorial_material": 0,
                "front_matter": 0,
                "other_excluded": 0,
                "catalog_content_items": 10,
            },
            issue["content_counts"],
        )
        self.assertEqual(10, issue["quality"]["official_item_count"])
        self.assertEqual(9, issue["quality"]["publishable_item_count"])
        self.assertEqual(1, issue["quality"]["excluded_item_count"])
        excluded = issue["quality"]["excluded_items"][0]
        self.assertEqual(self.dois[-1], excluded["doi"])
        self.assertEqual("correction", excluded["article_type"])
        self.assertEqual("correction", excluded["publisher_section"])
        self.assertIn("translation_incomplete", issue["quality"]["flags"])

    def test_configured_issue_alias_without_redirect_is_adjudicated(self):
        issue_content, article_pages = issue_page(self.dois)

        def fake_get(session, url, attempts=oup.MAX_ATTEMPTS):
            del session, attempts
            if url == CONFIGURED_ISSUE_URL:
                # The configured /issue route returns 26(5) directly: no redirect.
                return SimpleNamespace(content=issue_content, url=CONFIGURED_ISSUE_URL)
            return SimpleNamespace(content=article_pages[url], url=url)

        with patch.object(oup, "_get", side_effect=fake_get):
            issue = oup.fetch_current_issue("joeg")

        self.assertEqual(CONFIGURED_ISSUE_URL, issue["source_url"])
        self.assertEqual("joeg-26-5", issue["issue_id"])
        self.assertEqual(9, issue["research_article_count"])
        self.assertEqual(6, issue["content_counts"]["research_articles"])
        self.assertEqual(
            ["comment"] * 3 + ["research-article"] * 6,
            [article["article_type"] for article in issue["articles"]],
        )

    def test_configured_issue_alias_without_redirect_fails_closed_on_roster_drift(self):
        observed_dois = self.dois[:]
        observed_dois[0], observed_dois[1] = observed_dois[1], observed_dois[0]
        issue_content, article_pages = issue_page(observed_dois)

        def fake_get(session, url, attempts=oup.MAX_ATTEMPTS):
            del session, attempts
            if url == CONFIGURED_ISSUE_URL:
                return SimpleNamespace(content=issue_content, url=CONFIGURED_ISSUE_URL)
            return SimpleNamespace(content=article_pages[url], url=url)

        with patch.object(oup, "_get", side_effect=fake_get):
            with self.assertRaisesRegex(ValueError, "roster/order mismatch"):
                oup.fetch_current_issue("joeg")

    def test_normalized_readiness_counts_nine_rows_and_preserves_six_research_types(self):
        issue_content, article_pages = issue_page(self.dois)

        def fake_get(session, url, attempts=oup.MAX_ATTEMPTS):
            del session, attempts
            if url == CONFIGURED_ISSUE_URL:
                return SimpleNamespace(content=issue_content, url=CONFIGURED_ISSUE_URL)
            return SimpleNamespace(content=article_pages[url], url=url)

        with patch.object(oup, "_get", side_effect=fake_get):
            issue = oup.fetch_current_issue("joeg")

        # Every mock detail is deliberately complete; this is a pipeline contract
        # test and does not claim the real publisher metadata is complete.
        self.assertTrue(is_detected_snapshot(issue))
        self.assertTrue(is_publishable_snapshot(issue))
        for sequence, article in enumerate(issue["articles"], start=1):
            translated = {
                "title_cn": f"JOEG测试文章 {sequence}",
                "abstract_cn": (
                    f"这段官方测试摘要 {sequence} 介绍处理回归使用的虚构经济地理主题，"
                    "并用于检验分类与翻译计数流程。"
                ),
            }
            validate_translation(article, translated)
            article.update(translated)

        normalized = normalize_issue_content(issue)

        self.assertEqual(9, len(normalized["articles"]))
        self.assertEqual(9, normalized["research_article_count"])
        self.assertEqual(9, normalized["expected_article_count"])
        self.assertEqual(6, normalized["content_counts"]["research_articles"])
        self.assertEqual(3, normalized["content_counts"]["comments"])
        self.assertEqual(9, normalized["content_counts"]["publishable_items"])
        self.assertEqual(1, normalized["content_counts"]["corrections"])
        self.assertEqual(1, normalized["quality"]["excluded_item_count"])
        self.assertEqual(9, normalized["quality"]["abstract_en_complete"])
        self.assertEqual(9, normalized["quality"]["authors_complete"])
        self.assertEqual(9, normalized["quality"]["doi_complete"])
        self.assertEqual(9, normalized["quality"]["translation_complete"])
        self.assertTrue(is_detected_snapshot(normalized))
        self.assertTrue(is_publishable_snapshot(normalized))
        self.assertEqual("complete", issue_content_status(normalized))
        self.assertEqual("ready", normalized["content_status"])
        self.assertEqual("ready", normalized["publication_state"])

    def test_changed_roster_order_duplicate_missing_and_unknown_doi_fail_closed(self):
        cases = {
            "reordered": self.dois[:],
            "duplicate": self.dois[:],
            "missing": self.dois[:-1],
            "unknown": self.dois[:],
        }
        cases["reordered"][0], cases["reordered"][1] = (
            cases["reordered"][1],
            cases["reordered"][0],
        )
        cases["duplicate"][1] = cases["duplicate"][0]
        cases["unknown"][4] = "10.1093/jeg/not-in-this-issue"

        for label, dois in cases.items():
            with self.subTest(label=label):
                parsed = {
                    "volume": "26",
                    "issue": "5",
                    "article_items": [
                        {"source_sequence": i, "doi": doi}
                        for i, doi in enumerate(dois, start=1)
                    ],
                }
                with self.assertRaisesRegex(ValueError, "roster/order mismatch"):
                    oup._adjudicate_joeg_26_5(parsed, ISSUE_URL)

    def test_wrong_volume_or_issue_fails_closed(self):
        for volume, issue_number in (("27", "5"), ("26", "6")):
            with self.subTest(volume=volume, issue=issue_number):
                parsed = {
                    "volume": volume,
                    "issue": issue_number,
                    "article_items": [
                        {"source_sequence": i, "doi": doi}
                        for i, doi in enumerate(self.dois, start=1)
                    ],
                }
                with self.assertRaisesRegex(ValueError, "issue identity mismatch"):
                    oup._adjudicate_joeg_26_5(parsed, ISSUE_URL)

    def test_comment_type_survives_detail_fetch_failure(self):
        inventory_item = {
            "source_sequence": 1,
            "title_en": "A JOEG commentary",
            "doi": self.dois[0],
            "source_url": "https://academic.oup.com/joeg/article/26/5/651/fixture",
            "canonical_article_type": "comment",
        }
        article = oup._detail_fallback(
            inventory_item,
            requests.Timeout("offline simulated timeout"),
        )
        self.assertEqual("comment", article["article_type"])


if __name__ == "__main__":
    unittest.main()
