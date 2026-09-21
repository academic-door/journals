from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from scripts.build_sciencedirect_browser_archives import (
    _apply_repec_publisher_abstract_fallbacks,
    fetch_issue_metadata,
    issue_from_roster,
    process_batch,
)


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

    def test_issue_identity_uses_issue_specific_url_and_issue_id(self) -> None:
        roster = {
            "journal_id": "joe",
            "issue_id": "joe-235-2",
            "official_url": "https://www.sciencedirect.com/journal/journal-of-econometrics/vol/235/issue/2",
        }
        self.assertEqual("2", issue_from_roster(roster))

    def test_issue_identity_uses_supplement_url(self) -> None:
        roster = {
            "journal_id": "red",
            "issue_id": "red-47-c",
            "official_url": "https://www.sciencedirect.com/journal/review-of-economic-dynamics/vol/47/suppl/C",
        }
        self.assertEqual("C", issue_from_roster(roster))

    def test_issue_identity_mismatch_fails_closed(self) -> None:
        roster = {
            "journal_id": "joe",
            "issue_id": "joe-235-2",
            "issue": "C",
            "official_url": "https://www.sciencedirect.com/journal/journal-of-econometrics/vol/235/issue/2",
        }
        with self.assertRaisesRegex(ValueError, "issue identity mismatch"):
            issue_from_roster(roster)


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

    def test_repec_publisher_supplied_abstract_fallback_requires_exact_identity(self) -> None:
        roster = {"journal_id": "ecolecon", "issue_id": "ecolecon-225-c"}
        by_pii = {
            "S0921800924002209": {
                "doi": "10.1016/j.ecolecon.2024.108323",
                "abstract_en": "",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "ecolecon" / "ecolecon-225-c.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "doi": "10.1016/j.ecolecon.2024.108323",
                                "source_url": "https://www.sciencedirect.com/science/article/pii/S0921800924002209",
                                "abstract_en": "Publisher supplied abstract.",
                                "sources": {
                                    "abstract_en": "repec-publisher-supplied",
                                    "repec": "https://ideas.repec.org/a/eee/ecolec/example.html",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _apply_repec_publisher_abstract_fallbacks(roster, by_pii, staging_root=root)
        self.assertEqual("Publisher supplied abstract.", by_pii["S0921800924002209"]["abstract_en"])
        self.assertEqual("repec-publisher-supplied", by_pii["S0921800924002209"]["abstract_source"])

        mismatch = {
            "S0921800924002209": {
                "doi": "10.1016/j.ecolecon.2024.WRONG",
                "abstract_en": "",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "ecolecon" / "ecolecon-225-c.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "doi": "10.1016/j.ecolecon.2024.108323",
                                "source_url": "https://www.sciencedirect.com/science/article/pii/S0921800924002209",
                                "abstract_en": "Must not cross identity.",
                                "sources": {
                                    "abstract_en": "repec-publisher-supplied",
                                    "repec": "https://ideas.repec.org/a/eee/ecolec/example.html",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _apply_repec_publisher_abstract_fallbacks(roster, mismatch, staging_root=root)
        self.assertEqual("", mismatch["S0921800924002209"]["abstract_en"])

    def test_repec_fallback_can_restore_missing_api_doi_with_exact_title_identity(self) -> None:
        roster = {
            "journal_id": "ecolecon",
            "issue_id": "ecolecon-225-c",
            "items": [
                {
                    "title": "Navigating sustainable futures: The role of terminal and instrumental values",
                    "href": "https://www.sciencedirect.com/science/article/pii/S0921800924002222",
                }
            ],
        }
        by_pii = {
            "S0921800924002222": {
                "doi": "",
                "title_en": "Navigating sustainable futures: The role of terminal and instrumental values",
                "abstract_en": "",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "ecolecon" / "ecolecon-225-c.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "doi": "10.1016/j.ecolecon.2024.108325",
                                "title_en": "Navigating sustainable futures: The role of terminal and instrumental values",
                                "source_url": "https://www.sciencedirect.com/science/article/pii/S0921800924002222",
                                "abstract_en": "Publisher supplied abstract.",
                                "sources": {
                                    "abstract_en": "repec-publisher-supplied",
                                    "repec": "https://ideas.repec.org/a/eee/ecolec/example.html",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _apply_repec_publisher_abstract_fallbacks(roster, by_pii, staging_root=root)

        item = by_pii["S0921800924002222"]
        self.assertEqual("10.1016/j.ecolecon.2024.108325", item["doi"])
        self.assertEqual("Publisher supplied abstract.", item["abstract_en"])
        self.assertEqual("repec-publisher-supplied", item["doi_source"])
        self.assertEqual("repec-publisher-supplied", item["abstract_source"])

    def test_missing_api_doi_fallback_rejects_title_mismatch(self) -> None:
        roster = {
            "journal_id": "ecolecon",
            "issue_id": "ecolecon-225-c",
            "items": [
                {
                    "title": "Official title",
                    "href": "https://www.sciencedirect.com/science/article/pii/S0921800924002222",
                }
            ],
        }
        by_pii = {
            "S0921800924002222": {
                "doi": "",
                "title_en": "Different API title",
                "abstract_en": "",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "ecolecon" / "ecolecon-225-c.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "doi": "10.1016/j.ecolecon.2024.108325",
                                "title_en": "Official title",
                                "source_url": "https://www.sciencedirect.com/science/article/pii/S0921800924002222",
                                "abstract_en": "Must stay unused.",
                                "sources": {
                                    "abstract_en": "repec-publisher-supplied",
                                    "repec": "https://ideas.repec.org/a/eee/ecolec/example.html",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _apply_repec_publisher_abstract_fallbacks(roster, by_pii, staging_root=root)
        self.assertEqual("", by_pii["S0921800924002222"]["doi"])
        self.assertEqual("", by_pii["S0921800924002222"]["abstract_en"])


    def test_process_batch_defers_one_issue_and_continues(self) -> None:
        blocked = Path("blocked.json")
        accepted = Path("accepted.json")
        with (
            patch(
                "scripts.build_sciencedirect_browser_archives.process",
                side_effect=[
                    ValueError("snapshot source-integrity gate failed: missing abstract"),
                    {"issue_id": "accepted", "publication_state": "ready"},
                ],
            ),
            patch(
                "scripts.build_sciencedirect_browser_archives.read_json",
                return_value={"issue_id": "blocked"},
            ),
        ):
            results, deferred = process_batch(
                [blocked, accepted],
                state_root=Path("state"),
                cache_root=Path("cache"),
                translate=True,
            )

        self.assertEqual(["accepted"], [item["issue_id"] for item in results])
        self.assertEqual("blocked", deferred[0]["issue_id"])
        self.assertIn("missing abstract", deferred[0]["error"])

    def test_process_batch_reports_all_deferred_without_accepting_any(self) -> None:
        with (
            patch(
                "scripts.build_sciencedirect_browser_archives.process",
                side_effect=ValueError("blocked"),
            ),
            patch(
                "scripts.build_sciencedirect_browser_archives.read_json",
                return_value={"issue_id": "blocked"},
            ),
        ):
            results, deferred = process_batch(
                [Path("one.json"), Path("two.json")],
                state_root=Path("state"),
                cache_root=Path("cache"),
                translate=False,
            )
        self.assertEqual([], results)
        self.assertEqual(2, len(deferred))


if __name__ == "__main__":
    unittest.main()
