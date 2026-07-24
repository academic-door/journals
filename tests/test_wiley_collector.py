from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import wiley


FIXTURES = Path(__file__).resolve().parent / "fixtures"
CURRENT_URL = "https://onlinelibrary.wiley.com/toc/14680262/current"
RESOLVED_ISSUE_URL = (
    "https://onlinelibrary.wiley.com/toc/14680262/2026/94/2"
)
ARTICLE_1_URL = "https://onlinelibrary.wiley.com/doi/10.3982/ecta11111"
ARTICLE_2_URL = "https://onlinelibrary.wiley.com/doi/10.3982/ecta22222"
RETRIEVED_AT = "2026-07-24T08:00:00+00:00"


class FakeResponse:
    def __init__(
        self,
        content: bytes = b"",
        *,
        status_code: int = 200,
        url: str = CURRENT_URL,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}


class FakeSession:
    def __init__(self, outcomes: list[FakeResponse | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, object]] = []

    def get(self, url: str, timeout: object) -> FakeResponse:
        self.calls.append((url, timeout))
        if not self.outcomes:
            raise AssertionError("FakeSession received an unexpected request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def schema_errors(issue: dict) -> list[str]:
    project_schema = ROOT / "schemas" / "issue.schema.json"
    schema_path = (
        project_schema
        if project_schema.exists()
        else FIXTURES / "issue.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [error.message for error in validator.iter_errors(issue)]


def successful_fake_get(
    _session: object,
    url: str,
    **_kwargs: object,
) -> FakeResponse:
    mapping = {
        CURRENT_URL: FakeResponse(
            fixture_bytes("wiley_issue.html"), url=RESOLVED_ISSUE_URL
        ),
        ARTICLE_1_URL: FakeResponse(
            fixture_bytes("wiley_article_1.html"), url=ARTICLE_1_URL
        ),
        ARTICLE_2_URL: FakeResponse(
            fixture_bytes("wiley_article_2.html"), url=ARTICLE_2_URL
        ),
    }
    try:
        return mapping[url]
    except KeyError as exc:
        raise AssertionError(f"Unexpected URL: {url}") from exc


class WileyCollectorTests(unittest.TestCase):
    def test_original_articles_order_and_exclusions_are_auditable(self):
        with patch.object(wiley, "_get", side_effect=successful_fake_get):
            issue = wiley.fetch_current_issue(
                CURRENT_URL,
                max_workers=2,
                retrieved_at=RETRIEVED_AT,
            )

        self.assertEqual([], schema_errors(issue))
        self.assertEqual("ecta-94-2", issue["issue_id"])
        self.assertEqual(("94", "2"), (issue["volume"], issue["issue"]))
        self.assertEqual("March 2026", issue["publication_date"])
        self.assertEqual(2, issue["expected_article_count"])
        self.assertEqual(2, issue["research_article_count"])
        self.assertEqual(
            [
                "First Ordered Research Article",
                "Second Ordered Research Article",
            ],
            [article["title_en"] for article in issue["articles"]],
        )
        self.assertEqual([1, 2], [article["sequence"] for article in issue["articles"]])
        self.assertEqual(
            [2, 3], [article["source_sequence"] for article in issue["articles"]]
        )
        self.assertEqual(
            ["10.3982/ecta11111", "10.3982/ecta22222"],
            [article["doi"] for article in issue["articles"]],
        )
        self.assertEqual(["Alice Alpha", "Beta Two"], issue["articles"][0]["authors"])
        self.assertIn("first synthetic abstract", issue["articles"][0]["abstract_en"])
        self.assertEqual(5, issue["quality"]["official_item_count"])
        self.assertEqual(3, issue["quality"]["excluded_item_count"])
        self.assertEqual(
            [
                (1, "front-matter"),
                (4, "section-not-original-articles"),
                (5, "back-matter"),
            ],
            [
                (item["source_sequence"], item["reason"])
                for item in issue["quality"]["excluded_items"]
            ],
        )
        self.assertTrue(issue["quality"]["roster_match"])
        self.assertTrue(issue["quality"]["order_preserved"])
        self.assertEqual(2, issue["quality"]["doi_complete"])
        self.assertEqual(2, issue["quality"]["authors_complete"])
        self.assertEqual(2, issue["quality"]["abstract_en_complete"])
        self.assertEqual(["translation_incomplete"], issue["quality"]["flags"])
        self.assertEqual("incomplete", issue["status"])

    def test_article_page_selector_fallbacks_extract_official_metadata(self):
        detail = wiley._parse_article_page(
            fixture_bytes("wiley_article_2.html"), ARTICLE_2_URL
        )
        self.assertEqual("Second Ordered Research Article", detail["title"])
        self.assertEqual(["Gamma Three"], detail["authors"])
        self.assertEqual("10.3982/ecta22222", detail["doi"])
        self.assertEqual(("94", "2"), (detail["volume"], detail["issue"]))
        self.assertIn("second synthetic abstract", detail["abstract"])

    def test_detail_failure_keeps_roster_and_reports_machine_readable_state(self):
        def fake_get(_session: object, url: str, **_kwargs: object) -> FakeResponse:
            if url == ARTICLE_2_URL:
                raise wiley.WileyFetchError(
                    "publisher_access_blocked",
                    "Synthetic 403",
                    url=url,
                    attempts=4,
                    status_code=403,
                )
            return successful_fake_get(_session, url, **_kwargs)

        with patch.object(wiley, "_get", side_effect=fake_get):
            issue = wiley.fetch_current_issue(
                CURRENT_URL,
                max_workers=1,
                retrieved_at=RETRIEVED_AT,
            )

        self.assertEqual([], schema_errors(issue))
        self.assertEqual(2, issue["research_article_count"])
        self.assertTrue(issue["quality"]["roster_match"])
        self.assertEqual(1, issue["quality"]["detail_failure_count"])
        self.assertEqual(
            [
                {
                    "source_sequence": 3,
                    "code": "publisher_access_blocked",
                    "attempts": 4,
                    "http_status": 403,
                }
            ],
            issue["quality"]["detail_failures"],
        )
        self.assertIn("article_detail_fetch_incomplete", issue["quality"]["flags"])
        failed_article = issue["articles"][1]
        self.assertEqual(["Gamma Three"], failed_article["authors"])
        self.assertEqual("10.3982/ecta22222", failed_article["doi"])
        self.assertEqual("", failed_article["abstract_en"])
        self.assertIn(
            "detail_fetch_failed:publisher_access_blocked",
            failed_article["quality_flags"],
        )
        self.assertEqual("blocked", failed_article["translation"]["status"])

    def test_retryable_responses_honor_retry_after_and_then_succeed(self):
        session = FakeSession(
            [
                FakeResponse(
                    status_code=429,
                    url=CURRENT_URL,
                    headers={"Retry-After": "2"},
                ),
                FakeResponse(status_code=503, url=CURRENT_URL),
                FakeResponse(b"<html>ok</html>", url=CURRENT_URL),
            ]
        )
        delays: list[float] = []

        response = wiley._get(
            session,
            CURRENT_URL,
            attempts=3,
            timeout=(1, 2),
            sleep_fn=delays.append,
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual([2.0, 3.0], delays)
        self.assertEqual(3, len(session.calls))
        self.assertTrue(all(timeout == (1, 2) for _url, timeout in session.calls))

    def test_timeout_is_retried_with_bounded_attempt_count(self):
        session = FakeSession(
            [
                requests.Timeout("synthetic timeout"),
                FakeResponse(b"<html>ok</html>", url=CURRENT_URL),
            ]
        )
        delays: list[float] = []

        response = wiley._get(
            session,
            CURRENT_URL,
            attempts=2,
            timeout=(0.1, 0.2),
            sleep_fn=delays.append,
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual([1.5], delays)
        self.assertEqual(2, len(session.calls))

    def test_repeated_403_returns_schema_compatible_error_issue(self):
        session = FakeSession(
            [
                FakeResponse(status_code=403, url=CURRENT_URL),
                FakeResponse(status_code=403, url=CURRENT_URL),
            ]
        )
        with patch.object(wiley, "_session", return_value=session):
            issue = wiley.fetch_current_issue(
                CURRENT_URL,
                attempts=2,
                sleep_fn=lambda _seconds: None,
                retrieved_at=RETRIEVED_AT,
            )

        self.assertEqual([], schema_errors(issue))
        self.assertEqual("error", issue["status"])
        self.assertEqual([], issue["articles"])
        self.assertEqual(
            "publisher_access_blocked", issue["quality"]["error"]["code"]
        )
        self.assertEqual(2, issue["quality"]["error"]["attempts"])
        self.assertEqual(403, issue["quality"]["error"]["http_status"])
        self.assertEqual(
            ["collector_error:publisher_access_blocked"],
            issue["quality"]["flags"],
        )

    def test_200_browser_challenge_is_reported_as_access_blocked(self):
        challenge = b"<html><title>Just a moment...</title><div id='challenge-form'></div>"
        session = FakeSession([FakeResponse(challenge, url=CURRENT_URL)])
        with patch.object(wiley, "_session", return_value=session):
            issue = wiley.fetch_current_issue(
                CURRENT_URL,
                attempts=1,
                sleep_fn=lambda _seconds: None,
                retrieved_at=RETRIEVED_AT,
            )

        self.assertEqual("error", issue["status"])
        self.assertEqual(
            "publisher_access_blocked", issue["quality"]["error"]["code"]
        )

    def test_unrecognized_issue_structure_fails_closed(self):
        html = b"<html><h1>Econometrica latest papers</h1><a href='/doi/10.3982/ECTA1'>Paper</a></html>"
        with patch.object(
            wiley,
            "_get",
            return_value=FakeResponse(html, url=RESOLVED_ISSUE_URL),
        ):
            issue = wiley.fetch_current_issue(
                CURRENT_URL,
                retrieved_at=RETRIEVED_AT,
            )

        self.assertEqual("error", issue["status"])
        self.assertEqual(
            "original_articles_section_missing",
            issue["quality"]["error"]["code"],
        )

    def test_anchor_fallback_preserves_order_after_wiley_markup_change(self):
        issue_html = b"""
        <html><head>
          <meta name="citation_volume" content="94">
          <meta name="citation_issue" content="2">
        </head><body>
          <h2 class="section-title">Original Articles</h2>
          <p><a href="/doi/10.3982/ECTA11111">First Ordered Research Article</a></p>
          <p><a href="/doi/10.3982/ECTA22222">Second Ordered Research Article</a></p>
        </body></html>
        """

        def fake_get(
            _session: object, url: str, **_kwargs: object
        ) -> FakeResponse:
            if url == CURRENT_URL:
                return FakeResponse(issue_html, url=RESOLVED_ISSUE_URL)
            return successful_fake_get(_session, url, **_kwargs)

        with patch.object(wiley, "_get", side_effect=fake_get):
            issue = wiley.fetch_current_issue(
                CURRENT_URL,
                max_workers=2,
                retrieved_at=RETRIEVED_AT,
            )

        self.assertEqual(
            [ARTICLE_1_URL, ARTICLE_2_URL],
            [article["source_url"] for article in issue["articles"]],
        )
        self.assertEqual([1, 2], [article["source_sequence"] for article in issue["articles"]])
        self.assertTrue(issue["quality"]["order_preserved"])

    def test_non_wiley_url_is_rejected_before_network_access(self):
        session = FakeSession([])
        with self.assertRaises(wiley.WileyFetchError) as context:
            wiley._get(session, "https://doi.org/10.3982/ECTA11111")
        self.assertEqual("non_official_url", context.exception.code)
        self.assertEqual([], session.calls)


if __name__ == "__main__":
    unittest.main()
