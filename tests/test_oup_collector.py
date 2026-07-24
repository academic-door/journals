import html
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import oup


FIXTURES = ROOT / "tests" / "fixtures" / "oup"
SCHEMA = json.loads(
    (ROOT / "schemas" / "issue.schema.json").read_text(encoding="utf-8")
)


class FakeResponse:
    def __init__(
        self,
        content: bytes | str,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        url: str = "",
    ):
        self.content = (
            content.encode("utf-8") if isinstance(content, str) else content
        )
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(
                f"{self.status_code} response", response=response
            )


def build_fixture_network(
    fixture_name: str,
    issue_url: str,
) -> tuple[bytes, dict[str, bytes], list[str]]:
    issue_content = (FIXTURES / fixture_name).read_bytes()
    soup = BeautifulSoup(issue_content, "html.parser")
    article_pages: dict[str, bytes] = {}
    ordered_research_titles: list[str] = []

    for card in soup.select(".al-article-item-wrap"):
        title_link = card.select_one(".al-title-list a")
        assert title_link is not None
        title = " ".join(title_link.get_text(" ", strip=True).split())
        article_type_node = card.select_one(".al-article-type")
        article_type = (
            article_type_node.get_text(" ", strip=True)
            if article_type_node
            else ""
        )
        if oup._exclusion_reason(title, article_type):
            continue

        article_url = urljoin(issue_url, str(title_link["href"]))
        doi_link = card.select_one("a[href*='doi.org/']")
        assert doi_link is not None
        doi = doi_link.get_text(" ", strip=True)
        detail = f"""<!doctype html>
<html lang="en">
  <head>
    <meta name="citation_title" content="{html.escape(title)}">
    <meta name="citation_author" content="Ada Researcher">
    <meta name="citation_author" content="Ben Economist">
    <meta name="citation_doi" content="{html.escape(doi)}">
    <meta name="citation_publication_date" content="2026-01-01">
  </head>
  <body>
    <main>
      <h1 class="wi-article-title">{html.escape(title)}</h1>
      <section class="abstract"><h2>Abstract</h2>
        <p>Offline fixture abstract for {html.escape(title)}.</p>
      </section>
    </main>
  </body>
</html>"""
        article_pages[article_url] = detail.encode("utf-8")
        ordered_research_titles.append(title)
    return issue_content, article_pages, ordered_research_titles


def assert_matches_issue_schema(test_case: unittest.TestCase, issue: dict) -> None:
    validator = Draft202012Validator(
        SCHEMA,
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(issue),
        key=lambda error: list(error.path),
    )
    test_case.assertEqual([], [error.message for error in errors])


class OxfordAcademicCollectorTests(unittest.TestCase):
    def test_qje_preserves_official_order_and_excludes_non_research_items(self):
        issue_url = "https://academic.oup.com/qje/issue"
        issue_content, article_pages, ordered_titles = build_fixture_network(
            "qje_issue.html", issue_url
        )

        def fake_get(session, url, attempts=oup.MAX_ATTEMPTS):
            del session, attempts
            if url == issue_url:
                return FakeResponse(issue_content, url=url)
            return FakeResponse(article_pages[url], url=url)

        with patch.object(oup, "_get", side_effect=fake_get):
            issue = oup.fetch_current_issue("qje")

        self.assertEqual("qje", issue["journal_id"])
        self.assertEqual("qje-141-3", issue["issue_id"])
        self.assertEqual(("141", "3"), (issue["volume"], issue["issue"]))
        self.assertEqual("August 2026", issue["publication_date"])
        self.assertEqual(2, issue["expected_article_count"])
        self.assertEqual(2, issue["research_article_count"])
        self.assertEqual(
            ordered_titles,
            [article["title_en"] for article in issue["articles"]],
        )
        self.assertEqual([1, 2], [article["sequence"] for article in issue["articles"]])
        self.assertEqual(
            ["Front Matter", "Table of Contents"],
            [item["title_en"] for item in issue["quality"]["excluded_items"]],
        )
        self.assertEqual(4, issue["quality"]["official_item_count"])
        self.assertEqual(2, issue["quality"]["excluded_item_count"])
        self.assertTrue(issue["quality"]["roster_match"])
        self.assertTrue(issue["quality"]["order_preserved"])
        self.assertEqual(2, issue["quality"]["doi_complete"])
        self.assertEqual(2, issue["quality"]["authors_complete"])
        self.assertEqual(2, issue["quality"]["abstract_en_complete"])
        self.assertEqual(
            ["Ada Researcher", "Ben Economist"],
            issue["articles"][0]["authors"],
        )
        self.assertEqual("10.1093/qje/qjag027", issue["articles"][0]["doi"])
        self.assertEqual(["translation_incomplete"], issue["quality"]["flags"])
        assert_matches_issue_schema(self, issue)

    def test_restud_and_res_aliases_share_one_adapter(self):
        issue_url = "https://academic.oup.com/restud/issue"
        issue_content, article_pages, ordered_titles = build_fixture_network(
            "restud_issue.html", issue_url
        )

        def fake_get(session, url, attempts=oup.MAX_ATTEMPTS):
            del session, attempts
            if url == issue_url:
                return FakeResponse(issue_content, url=url)
            return FakeResponse(article_pages[url], url=url)

        for journal_alias in ("restud", "res", issue_url):
            with self.subTest(journal_alias=journal_alias):
                with patch.object(oup, "_get", side_effect=fake_get):
                    issue = oup.fetch_current_issue(journal_alias)

                self.assertEqual("res", issue["journal_id"])
                self.assertEqual("res-93-4", issue["issue_id"])
                self.assertEqual("July 2026", issue["publication_date"])
                self.assertEqual(2, issue["research_article_count"])
                self.assertEqual(
                    ordered_titles,
                    [article["title_en"] for article in issue["articles"]],
                )
                self.assertEqual(3, issue["quality"]["official_item_count"])
                self.assertEqual(1, issue["quality"]["excluded_item_count"])
                self.assertEqual(2, issue["quality"]["doi_complete"])
                self.assertEqual(2, issue["quality"]["authors_complete"])
                self.assertEqual(2, issue["quality"]["abstract_en_complete"])
                assert_matches_issue_schema(self, issue)

    def test_detail_failure_preserves_issue_metadata_and_marks_incomplete(self):
        issue_url = "https://academic.oup.com/qje/issue"
        issue_content, article_pages, _ = build_fixture_network(
            "qje_issue.html", issue_url
        )
        failed_url = next(iter(article_pages))

        def fake_get(session, url, attempts=oup.MAX_ATTEMPTS):
            del session, attempts
            if url == issue_url:
                return FakeResponse(issue_content, url=url)
            if url == failed_url:
                raise requests.Timeout("offline simulated timeout")
            return FakeResponse(article_pages[url], url=url)

        with patch.object(oup, "_get", side_effect=fake_get):
            issue = oup.fetch_current_issue("qje")

        failed = issue["articles"][0]
        self.assertEqual("The Power of Proximity to Coworkers", failed["title_en"])
        self.assertEqual("10.1093/qje/qjag027", failed["doi"])
        self.assertEqual([], failed["authors"])
        self.assertIn("detail_fetch_failed:Timeout", failed["quality_flags"])
        self.assertEqual(1, issue["quality"]["detail_failure_count"])
        self.assertIn(
            "article_detail_fetch_incomplete",
            issue["quality"]["flags"],
        )
        assert_matches_issue_schema(self, issue)

    def test_article_page_dom_fallback_extracts_metadata(self):
        content = b"""<!doctype html>
<html><body><main>
  <h1 class="wi-article-title">The Power of Proximity to Coworkers</h1>
  <div class="al-authors-list">
    <a>Natalia Emanuel</a><a>Emma Harrington</a><a>Amanda Pallais</a>
  </div>
  <section class="abstract"><h2>Abstract</h2>
    <p>Offline fixture abstract used to test DOM fallbacks.</p>
  </section>
  <p>DOI: 10.1093/qje/qjag027</p>
</main></body></html>"""
        inventory = {
            "source_sequence": 1,
            "title_en": "The Power of Proximity to Coworkers",
            "doi": "",
            "source_url": (
                "https://academic.oup.com/qje/article/141/3/1825/8676722"
            ),
        }
        article = oup._parse_article_page(content, inventory)
        self.assertEqual(
            ["Natalia Emanuel", "Emma Harrington", "Amanda Pallais"],
            article["authors"],
        )
        self.assertEqual("10.1093/qje/qjag027", article["doi"])
        self.assertEqual(
            "Offline fixture abstract used to test DOM fallbacks.",
            article["abstract_en"],
        )

    def test_get_retries_transient_status_and_uses_explicit_timeout(self):
        class FakeSession:
            def __init__(self):
                self.calls = []
                self.responses = [
                    FakeResponse("", status_code=503),
                    FakeResponse("ok", status_code=200),
                ]

            def get(self, url, timeout):
                self.calls.append((url, timeout))
                return self.responses.pop(0)

        session = FakeSession()
        with patch.object(oup.time, "sleep") as sleep:
            response = oup._get(session, "https://academic.oup.com/qje/issue")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            [
                ("https://academic.oup.com/qje/issue", (10, 45)),
                ("https://academic.oup.com/qje/issue", (10, 45)),
            ],
            session.calls,
        )
        sleep.assert_called_once_with(1.5)

    def test_identifies_academic_door_and_rejects_non_official_url(self):
        session = oup._session()
        self.assertEqual(oup.USER_AGENT, session.headers["User-Agent"])
        with self.assertRaisesRegex(ValueError, "official academic.oup.com"):
            oup.fetch_current_issue(
                "qje",
                "https://example.com/qje/issue",
            )


if __name__ == "__main__":
    unittest.main()
