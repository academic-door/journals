from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import chicago


FIXTURES = Path(__file__).parent / "fixtures"
CURRENT_URL = "https://www.journals.uchicago.edu/toc/jpe/current"


class FakeResponse:
    def __init__(self, content: bytes = b"", status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class ChicagoCollectorTests(unittest.TestCase):
    def fetch_offline_issue(self) -> dict:
        pages = {
            CURRENT_URL: fixture("chicago_jpe_current.html"),
            f"{chicago.CHICAGO_ORIGIN}/doi/10.1086/740219": fixture(
                "chicago_article_740219.html"
            ),
            f"{chicago.CHICAGO_ORIGIN}/doi/10.1086/740221": fixture(
                "chicago_article_740221.html"
            ),
        }

        def fake_get(session, url, **kwargs):
            del session, kwargs
            return FakeResponse(pages[url])

        with patch.object(chicago, "_get", side_effect=fake_get):
            return chicago.fetch_current_issue(CURRENT_URL, max_workers=2)

    def test_official_roster_order_and_exclusion_audit(self):
        issue = self.fetch_offline_issue()

        self.assertEqual("jpe-134-7", issue["issue_id"])
        self.assertEqual("134", issue["volume"])
        self.assertEqual("7", issue["issue"])
        self.assertEqual("2026-07", issue["publication_date"])
        self.assertEqual(6, issue["quality"]["official_item_count"])
        self.assertEqual(4, issue["quality"]["excluded_item_count"])
        self.assertEqual(2, issue["research_article_count"])
        self.assertTrue(issue["quality"]["roster_match"])
        self.assertTrue(issue["quality"]["order_preserved"])

        self.assertEqual([1, 2], [item["sequence"] for item in issue["articles"]])
        self.assertEqual(
            [2, 4], [item["source_sequence"] for item in issue["articles"]]
        )
        self.assertEqual(
            [
                "Training, Communications Patterns, and Spillovers inside Organizations",
                "Markets and Measurement",
            ],
            [item["title_en"] for item in issue["articles"]],
        )
        self.assertEqual(
            [
                "front-matter",
                "comment",
                "turnaround-times",
                "recent-referees",
            ],
            [
                item["article_type"]
                for item in issue["quality"]["excluded_items"]
            ],
        )
        self.assertEqual(
            [1, 3, 5, 6],
            [
                item["source_sequence"]
                for item in issue["quality"]["excluded_items"]
            ],
        )

    def test_article_metadata_uses_official_detail_pages(self):
        issue = self.fetch_offline_issue()
        first, second = issue["articles"]

        self.assertEqual(
            ["Miguel Espinosa", "Christopher Stanton"], first["authors"]
        )
        self.assertEqual("10.1086/740219", first["doi"])
        self.assertEqual("2026-05-27", first["publication_date"])
        self.assertIn("synthetic test abstract", first["abstract_en"])
        self.assertEqual("official-article-page", first["sources"]["authors"])
        self.assertEqual(["Ada Example", "Ben Sample"], second["authors"])
        self.assertEqual("10.1086/740221", second["doi"])
        self.assertEqual("2026-07", second["publication_date"])
        self.assertEqual(
            f"{chicago.CHICAGO_ORIGIN}/doi/10.1086/740221",
            second["source_url"],
        )

    def test_result_matches_repository_issue_schema(self):
        schema_value = os.environ.get("ACADEMIC_DOOR_ISSUE_SCHEMA", "")
        schema_path = (
            Path(schema_value)
            if schema_value
            else ROOT / "schemas" / "issue.schema.json"
        )
        if not schema_path.exists():
            self.skipTest(
                "Set ACADEMIC_DOOR_ISSUE_SCHEMA when testing outside the repository"
            )

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(
            validator.iter_errors(self.fetch_offline_issue()),
            key=lambda error: list(error.path),
        )
        self.assertEqual([], [error.message for error in errors])

    def test_non_research_classification(self):
        cases = (
            ("Front Matter", "", "front-matter"),
            ("JPE Turnaround Times", "Journal Information", "turnaround-times"),
            ("Recent Referees", "", "recent-referees"),
            ("Comment on “A Result”", "Comments", "comment"),
            ("A Comment on Identification", "", "comment"),
            ("A Research Article", "Articles", "research-article"),
        )
        for title, section, expected in cases:
            with self.subTest(title=title):
                article_type, _ = chicago.classify_toc_item(title, section)
                self.assertEqual(expected, article_type)

    def test_retry_timeout_and_user_agent(self):
        class FlakySession:
            def __init__(self):
                self.calls = []

            def get(self, url, timeout):
                self.calls.append((url, timeout))
                if len(self.calls) < 3:
                    raise requests.Timeout("temporary")
                return FakeResponse(b"ok")

        session = FlakySession()
        sleeps = []
        result = chicago._get(
            session,
            CURRENT_URL,
            attempts=3,
            timeout=17,
            backoff=0.25,
            sleeper=sleeps.append,
        )
        self.assertEqual(b"ok", result.content)
        self.assertEqual(
            [(CURRENT_URL, 17), (CURRENT_URL, 17), (CURRENT_URL, 17)],
            session.calls,
        )
        self.assertEqual([0.25, 0.5], sleeps)
        self.assertEqual(chicago.USER_AGENT, chicago._session().headers["User-Agent"])

    def test_detail_failure_is_visible_and_keeps_roster_item(self):
        issue_page = fixture("chicago_jpe_current.html")

        def fake_get(session, url, **kwargs):
            del session, kwargs
            if url == CURRENT_URL:
                return FakeResponse(issue_page)
            if url.endswith("740221"):
                return FakeResponse(fixture("chicago_article_740221.html"))
            raise requests.Timeout("publisher did not respond")

        with patch.object(chicago, "_get", side_effect=fake_get):
            issue = chicago.fetch_current_issue(CURRENT_URL, max_workers=1)

        self.assertEqual(2, issue["research_article_count"])
        self.assertEqual(1, issue["quality"]["detail_failure_count"])
        self.assertIn("article_detail_fetch_incomplete", issue["quality"]["flags"])
        self.assertEqual("unknown", issue["articles"][0]["article_type"])
        self.assertEqual(
            ["detail_fetch_failed:Timeout"],
            issue["articles"][0]["quality_flags"],
        )


@unittest.skipUnless(
    os.environ.get("ACADEMIC_DOOR_LIVE_SMOKE") == "1",
    "set ACADEMIC_DOOR_LIVE_SMOKE=1 for a read-only official-site smoke test",
)
class ChicagoLiveSmokeTests(unittest.TestCase):
    def test_jpe_current_issue_read_only(self):
        issue = chicago.fetch_current_issue(CURRENT_URL)
        self.assertEqual("jpe", issue["journal_id"])
        self.assertEqual(CURRENT_URL, issue["source_url"])
        self.assertTrue(issue["volume"])
        self.assertTrue(issue["issue"])
        self.assertGreater(issue["quality"]["official_item_count"], 0)


if __name__ == "__main__":
    unittest.main()
