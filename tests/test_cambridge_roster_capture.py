"""Tests for official Cambridge issue roster capture."""

import unittest

from scripts.build_recovery_queue import build_queue
from scripts.capture_cambridge_roster_evidence import (
    build_evidence,
    cambridge_all_issues_url,
    parse_cambridge_all_issues,
    parse_cambridge_issue,
    select_cambridge_records,
)

ALL_ISSUES_HTML = """
<html><body>
<a href="/core/journals/journal-of-economic-history/issue/ABC">Issue 1 March 2023 pp. 1-318</a>
<a href="/core/journals/journal-of-economic-history/issue/DEF">Issue 2 June 2023 pp. 319-644</a>
</body></html>
"""

ISSUE_HTML = """
<html><body>
<div class="product-listing-with-inputs-content">
  <a class="part-link" href="/core/journals/journal-of-economic-history/article/first/AAA">First Research Paper</a>
  <div class="author"><a class="more-by-this-author">Author One</a></div>
  <div class="altmetric-embed" data-doi="10.1017/S000000000000001"></div>
  <div id="abstractS000000000000001">Official abstract one.</div>
</div>
<div class="product-listing-with-inputs-content">
  <a class="part-link" href="/core/journals/journal-of-economic-history/article/second/BBB">Second Research Paper</a>
  <div class="author"><a class="more-by-this-author">Author Two</a></div>
  <div class="altmetric-embed" data-doi="10.1017/S000000000000002"></div>
  <div id="abstractS000000000000002">Official abstract two.</div>
</div>
<div class="product-listing-with-inputs-content">
  <a class="part-link" href="/core/journals/journal-of-economic-history/article/review/CCC">Book Title. By Reviewer. Cambridge: Press, 2023. Pp. 120.</a>
  <div class="author"><a class="more-by-this-author">Reviewer One</a></div>
  <div class="altmetric-embed" data-doi="10.1017/S000000000000003"></div>
</div>
<div class="product-listing-with-inputs-content">
  <a class="part-link" href="/core/journals/journal-of-economic-history/article/front/DDD">JEH volume 83 issue 1 Cover and Front matter</a>
  <div class="altmetric-embed" data-doi="10.1017/S000000000000004"></div>
</div>
</body></html>
"""


class CambridgeRosterCaptureTests(unittest.TestCase):
    def test_all_issues_maps_year_and_issue(self) -> None:
        issues = parse_cambridge_all_issues(
            ALL_ISSUES_HTML,
            base_url="https://www.cambridge.org/core/journals/journal-of-economic-history/all-issues",
        )
        self.assertEqual(
            "https://www.cambridge.org/core/journals/journal-of-economic-history/issue/ABC",
            issues[("2023", "1")],
        )
        self.assertEqual(
            "https://www.cambridge.org/core/journals/journal-of-economic-history/issue/DEF",
            issues[("2023", "2")],
        )

    def test_issue_parser_keeps_official_order_and_excludes_book_reviews(self) -> None:
        items, excluded = parse_cambridge_issue(
            ISSUE_HTML,
            base_url="https://www.cambridge.org/core/journals/journal-of-economic-history/issue/ABC",
        )
        self.assertEqual(2, len(items))
        self.assertEqual(2, len(excluded))
        self.assertEqual("10.1017/s000000000000001", items[0]["doi"])
        self.assertEqual("First Research Paper", items[0]["title_en"])
        self.assertEqual(["Author One"], items[0]["authors"])
        self.assertEqual("Official abstract one.", items[0]["abstract_en"])
        self.assertTrue(
            items[0]["source_url"].startswith("https://www.cambridge.org/core/journals/")
        )
        self.assertEqual("book-review", excluded[0]["reason"])
        self.assertEqual("front-matter-no-detail", excluded[1]["reason"])

    def test_source_pending_selection_uses_cambridge_host(self) -> None:
        manifest = {
            "records": [
                {
                    "issue_id": "jeh-83-1",
                    "journal": "JEH",
                    "source_status": "source_pending",
                    "official_url": "https://www.cambridge.org/core/journals/journal-of-economic-history",
                },
                {
                    "issue_id": "jf-78-1",
                    "journal": "JF",
                    "source_status": "source_pending",
                    "official_url": "https://onlinelibrary.wiley.com/toc/15406261/2023/78/1",
                },
            ]
        }
        selected = select_cambridge_records(
            manifest,
            output_root=__import__("pathlib").Path("unused"),
            skip_existing=False,
        )
        self.assertEqual(["jeh-83-1"], [record["issue_id"] for record in selected])

    def test_evidence_uses_exact_official_issue_url(self) -> None:
        record = {
            "journal": "JEH",
            "issue_id": "jeh-83-1",
        }
        evidence = build_evidence(
            record,
            official_url="https://www.cambridge.org/core/journals/journal-of-economic-history/issue/ABC",
            items=[
                {
                    "sequence": 1,
                    "doi": "10.1017/s000000000000001",
                    "title_en": "First Research Paper",
                    "authors": ["Author One"],
                    "abstract_en": "Official abstract one.",
                    "source_url": "https://www.cambridge.org/core/journals/journal-of-economic-history/article/first/AAA",
                }
            ],
            excluded=[],
            captured_at="2026-08-27T00:00:00+00:00",
        )
        self.assertEqual("https://www.cambridge.org/core/journals/journal-of-economic-history/issue/ABC", evidence["official_url"])

    def test_routes_cambridge_source_pending_to_evidence_adapter(self) -> None:
        _, shards = build_queue(
            {
                "records": [
                    {
                        "issue_id": "jeh-83-1",
                        "journal": "JEH",
                        "year": 2023,
                        "category": "source_pending",
                        "source_status": "source_pending",
                        "official_url": "https://www.cambridge.org/core/journals/journal-of-economic-history",
                    }
                ]
            },
            {"JEH": {"collector": "crossref"}},
            categories={"source_pending"},
            chunk_size=10,
        )
        self.assertEqual("cambridge-evidence", shards[0]["action"])
        self.assertEqual("https://www.cambridge.org/core/journals/journal-of-economic-history/all-issues", cambridge_all_issues_url(shards[0]["records"][0]))


if __name__ == "__main__":
    unittest.main()
