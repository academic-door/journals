from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from scripts.build_sciencedirect_browser_archives import fetch_issue_metadata


PII = "S0264837726002917"
XML = b'''<root xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
  <dc:title>Example article</dc:title>
  <dc:creator>Alice Example</dc:creator>
  <prism:doi>10.1016/j.example.2026.1</prism:doi>
  <description>Example abstract.</description>
</root>'''


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = XML, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, outcomes: list[object]):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ScienceDirectBrowserArchiveMetadataRetryTests(unittest.TestCase):
    def fetch(self, outcomes: list[object]):
        fake = FakeSession(outcomes)
        with (
            patch("scripts.build_sciencedirect_browser_archives.requests.Session", return_value=fake),
            patch("scripts.build_sciencedirect_browser_archives.time.sleep"),
        ):
            result = fetch_issue_metadata(requests.Session(), [PII], timeout=90)
        return fake, result

    def test_retries_transient_read_timeout_then_succeeds(self) -> None:
        fake, result = self.fetch(
            [requests.ReadTimeout("temporary Elsevier timeout"), FakeResponse(200)]
        )
        self.assertEqual(fake.calls, 2)
        self.assertEqual(result[PII]["doi"], "10.1016/j.example.2026.1")
        self.assertEqual(result[PII]["abstract_en"], "Example abstract.")

    def test_retries_transient_5xx_then_succeeds(self) -> None:
        fake, result = self.fetch([FakeResponse(503), FakeResponse(200)])
        self.assertEqual(fake.calls, 2)
        self.assertEqual(result[PII]["title_en"], "Example article")

    def test_static_4xx_remains_fail_closed_without_retry(self) -> None:
        fake = FakeSession([FakeResponse(404), FakeResponse(200)])
        with (
            patch("scripts.build_sciencedirect_browser_archives.requests.Session", return_value=fake),
            patch("scripts.build_sciencedirect_browser_archives.time.sleep"),
        ):
            with self.assertRaisesRegex(ValueError, r"HTTPError: HTTP 404"):
                fetch_issue_metadata(requests.Session(), [PII], timeout=90)
        self.assertEqual(fake.calls, 1)

    def test_exhausted_transport_retry_budget_stays_fatal(self) -> None:
        fake = FakeSession(
            [
                requests.ReadTimeout("timeout 1"),
                requests.ReadTimeout("timeout 2"),
                requests.ReadTimeout("timeout 3"),
            ]
        )
        with (
            patch("scripts.build_sciencedirect_browser_archives.requests.Session", return_value=fake),
            patch("scripts.build_sciencedirect_browser_archives.time.sleep"),
        ):
            with self.assertRaisesRegex(ValueError, rf"{PII} .*ReadTimeout"):
                fetch_issue_metadata(requests.Session(), [PII], timeout=90)
        self.assertEqual(fake.calls, 3)


if __name__ == "__main__":
    unittest.main()
