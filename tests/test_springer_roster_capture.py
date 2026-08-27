from __future__ import annotations

import unittest
from pathlib import Path

from scripts.capture_springer_roster_evidence import (
    build_evidence,
    parse_springer_detail,
    parse_springer_issue_links,
    select_springer_records,
    springer_issue_url,
    springer_issue_url_candidates,
)


class SpringerRosterCaptureTests(unittest.TestCase):
    ISSUE_HTML = """
    <ol>
      <li><a href="/article/10.1007/s10640-022-00706-w">First Research Paper</a></li>
      <li><a href="/article/10.1007/s10640-022-00708-8">Correction to: First Research Paper</a></li>
      <li><a href="https://link.springer.com/article/10.1007/s10640-022-00709-7">Second Research Paper</a></li>
    </ol>
    """

    DETAIL_HTML = """
    <meta name="citation_doi" content="10.1007/s10640-022-00706-w">
    <meta name="citation_title" content="First Research Paper">
    <meta name="citation_author" content="Author One">
    <meta name="citation_author" content="Author Two">
    <section id="Abs1"><h2>Abstract</h2><p>Official Springer abstract text.</p></section>
    """

    def test_issue_url_is_built_from_volumes_and_issues_page(self) -> None:
        self.assertEqual(
            "https://link.springer.com/journal/10640/volumes-and-issues/84-1",
            springer_issue_url(
                {
                    "issue_id": "ere-84-1",
                    "volume": "84",
                    "issue": "1",
                    "official_url": "https://link.springer.com/journal/10640/volumes-and-issues",
                }
            ),
        )

    def test_openurl_candidate_is_available_when_issn_is_known(self) -> None:
        urls = springer_issue_url_candidates(
            {
                "issue_id": "ere-85-3-4",
                "volume": "85",
                "issue": "3-4",
                "issn": "0924-6460",
                "official_url": "https://link.springer.com/journal/10640/volumes-and-issues",
            }
        )
        self.assertEqual(
            "https://link.springer.com/openurl?genre=journal&issn=0924-6460&volume=85&issue=3-4",
            urls[1],
        )

    def test_issue_parser_keeps_official_link_order(self) -> None:
        links = parse_springer_issue_links(
            self.ISSUE_HTML,
            base_url="https://link.springer.com/journal/10640/volumes-and-issues/84-1",
        )
        self.assertEqual(
            [
                "10.1007/s10640-022-00706-w",
                "10.1007/s10640-022-00708-8",
                "10.1007/s10640-022-00709-7",
            ],
            [link["doi"] for link in links],
        )
        self.assertEqual("First Research Paper", links[0]["title_en"])

    def test_detail_parser_reads_official_article_metadata(self) -> None:
        detail = parse_springer_detail(
            self.DETAIL_HTML,
            source_url="https://link.springer.com/article/10.1007/s10640-022-00706-w",
        )
        self.assertEqual("10.1007/s10640-022-00706-w", detail["doi"])
        self.assertEqual(["Author One", "Author Two"], detail["authors"])
        self.assertEqual("Official Springer abstract text.", detail["abstract_en"])

    def test_evidence_contains_details_and_excludes_corrections(self) -> None:
        details = [
            {
                "doi": "10.1007/s10640-022-00706-w",
                "title_en": "First Research Paper",
                "authors": ["Author One"],
                "abstract_en": "Official abstract one.",
                "source_url": "https://link.springer.com/article/10.1007/s10640-022-00706-w",
            },
            {
                "doi": "10.1007/s10640-022-00708-8",
                "title_en": "Correction to: First Research Paper",
                "authors": ["Author One"],
                "abstract_en": "Official correction text.",
                "source_url": "https://link.springer.com/article/10.1007/s10640-022-00708-8",
            },
        ]
        evidence = build_evidence(
            {"journal": "ERE", "issue_id": "ere-84-1"},
            official_url="https://link.springer.com/journal/10640/volumes-and-issues/84-1",
            details=details,
            captured_at="2026-08-27T00:00:00+00:00",
        )
        self.assertEqual(
            ["10.1007/s10640-022-00706-w"],
            [item["doi"] for item in evidence["items"]],
        )
        self.assertEqual(
            ["10.1007/s10640-022-00708-8"],
            [item["doi"] for item in evidence["excluded_items"]],
        )

    def test_source_pending_springer_record_selection_uses_source_dimension(self) -> None:
        records = select_springer_records(
            {
                "records": [
                    {
                        "journal": "ERE",
                        "issue_id": "ere-84-1",
                        "category": "translation_required",
                        "source_status": "source_pending",
                        "official_url": "https://link.springer.com/journal/10640/volumes-and-issues",
                    },
                    {
                        "journal": "JPE",
                        "issue_id": "jpe-131-10",
                        "source_status": "source_pending",
                        "official_url": "https://www.journals.uchicago.edu/toc/jpe/2023/131/10",
                    },
                ]
            },
            output_root=Path("unused"),
            skip_existing=False,
            issue_ids=None,
        )
        self.assertEqual(["ere-84-1"], [record["issue_id"] for record in records])

    def test_optional_issue_id_filter_limits_selected_records(self) -> None:
        records = select_springer_records(
            {
                "records": [
                    {
                        "journal": "ERE",
                        "issue_id": "ere-84-1",
                        "source_status": "source_pending",
                        "official_url": "https://link.springer.com/journal/10640/volumes-and-issues",
                    },
                    {
                        "journal": "ERE",
                        "issue_id": "ere-84-2",
                        "source_status": "source_pending",
                        "official_url": "https://link.springer.com/journal/10640/volumes-and-issues",
                    },
                ]
            },
            output_root=Path("unused"),
            skip_existing=False,
            issue_ids={"ere-84-2"},
        )
        self.assertEqual(["ere-84-2"], [record["issue_id"] for record in records])


if __name__ == "__main__":
    unittest.main()
