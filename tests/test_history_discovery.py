from __future__ import annotations

import unittest
from pathlib import Path

from collectors.history import (
    HistoricalIssue,
    discover_official_issues,
    historical_issue_sort_key,
    parse_archive,
)


class HistoryDiscoveryTests(unittest.TestCase):
    def test_numeric_issue_order_keeps_supplements_stable(self) -> None:
        labels = ["1", "10", "11", "12", "2", "3", "S1", "S2"]
        issues = [
            HistoricalIssue("AER", 2024, "114", label, f"https://example/{label}")
            for label in labels
        ]
        self.assertEqual(
            ["1", "2", "3", "10", "11", "12", "S1", "S2"],
            [item.issue for item in sorted(issues, key=historical_issue_sort_key)],
        )

    def test_discovers_aer_official_issue_links_and_filters_years(self) -> None:
        content = b"""
        <a href="/issues/828">December 2025 (Vol. 115, No. 12)</a>
        <a href="/issues/840">January 2026 (Vol. 116, No. 1)</a>
        <a href="https://example.com/issues/999">December 2025 (Vol. 115, No. 9)</a>
        """
        issues = parse_archive(
            content,
            "https://www.aeaweb.org/journals/aer/issues",
            journal="AER",
            platform="aea",
            years=[2025],
            allowed_host="www.aeaweb.org",
        )
        self.assertEqual(["aer-115-12"], [item.issue_id for item in issues])
        self.assertEqual("https://www.aeaweb.org/issues/828", issues[0].official_url)

    def test_discovers_chicago_oup_and_wiley_patterns(self) -> None:
        fixtures = [
            (
                b'<a href="/toc/jpe/2025/133/7">Volume 133 Number 7 July 2025</a>',
                "https://www.journals.uchicago.edu/loi/jpe",
                "JPE",
                "chicago",
                "www.journals.uchicago.edu",
                "jpe-133-7",
            ),
            (
                b'<a href="/qje/issue/140/3">Volume 140, Issue 3, August 2025</a>',
                "https://academic.oup.com/qje/issue-archive/2025",
                "QJE",
                "oup",
                "academic.oup.com",
                "qje-140-3",
            ),
            (
                b'<a href="/toc/14680262/2025/93/4">Volume 93, Issue 4</a>',
                "https://onlinelibrary.wiley.com/loi/14680262/year/2025",
                "ECTA",
                "wiley",
                "onlinelibrary.wiley.com",
                "ecta-93-4",
            ),
        ]
        for content, url, journal, platform, host, expected in fixtures:
            with self.subTest(platform=platform):
                issues = parse_archive(
                    content,
                    url,
                    journal=journal,
                    platform=platform,
                    years=[2025],
                    allowed_host=host,
                )
                self.assertEqual([expected], [item.issue_id for item in issues])

    def test_rejects_non_official_hosts(self) -> None:
        issues = parse_archive(
            b'<a href="https://mirror.example/qje/issue/140/1">'
            b"Volume 140, Issue 1, February 2025</a>",
            "https://academic.oup.com/qje/issue-archive/2025",
            journal="QJE",
            platform="oup",
            years=[2025],
            allowed_host="academic.oup.com",
        )
        self.assertEqual([], issues)

    def test_builds_only_declared_stable_official_issue_urls(self) -> None:
        issues = discover_official_issues(
            "JPE",
            {
                "platform": "chicago",
                "allowed_host": "www.journals.uchicago.edu",
                "issue_url_template": (
                    "https://www.journals.uchicago.edu/toc/jpe/"
                    "{year}/{volume}/{issue}"
                ),
                "year_ranges": {
                    "2025": {"volume": 133, "issues": [1, 2]},
                    "2026": {"volume": 134, "issues": [1]},
                },
            },
            years=[2025],
        )
        self.assertEqual(["jpe-133-1", "jpe-133-2"], [item.issue_id for item in issues])

    def test_qe_2023_and_2024_volumes_are_mapped_from_field_config(self) -> None:
        import yaml

        config = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config/field-history.yml").read_text(
                encoding="utf-8"
            )
        )
        qe = config["journals"]["QE"]
        self.assertIn(2023, config["years"])
        self.assertIn(2024, config["years"])
        self.assertEqual("14", qe["year_ranges"][2023]["volume"])
        self.assertEqual("15", qe["year_ranges"][2024]["volume"])
        issues = discover_official_issues(
            "QE",
            qe,
            years=[2023, 2024],
        )
        ids = [item.issue_id for item in issues]
        self.assertIn("qe-14-1", ids)
        self.assertIn("qe-15-4", ids)
        self.assertEqual(8, len(ids))

    def test_aer_and_jpe_2024_use_complete_official_discovery(self) -> None:
        import yaml

        config = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config/field-history.yml").read_text(
                encoding="utf-8"
            )
        )["journals"]
        aer = config["AER"]
        self.assertEqual("aea", aer["platform"])
        self.assertEqual(
            "https://www.aeaweb.org/journals/aer/issues",
            aer["archive_url"],
        )
        aer_archive = "".join(
            f'<a href="/issues/{700 + number}">Month 2024 '
            f'(Vol. 114, No. {number})</a>'
            for number in range(1, 13)
        ).encode()
        aer_issues = parse_archive(
            aer_archive,
            aer["archive_url"],
            journal="AER",
            platform="aea",
            years=[2024],
            allowed_host=aer["allowed_host"],
        )
        self.assertEqual(
            [f"aer-114-{number}" for number in range(1, 13)],
            [item.issue_id for item in aer_issues],
        )

        jpe = config["JPE"]
        self.assertEqual("year_ranges", jpe["platform"])
        jpe_issues = discover_official_issues("JPE", jpe, years=[2024])
        self.assertEqual(
            [f"jpe-132-{number}" for number in range(1, 13)],
            [item.issue_id for item in jpe_issues],
        )
        self.assertTrue(
            all(jpe["allowed_host"] in item.official_url for item in jpe_issues)
        )



class CrossrefDiscoveryTests(unittest.TestCase):
    def test_discovers_continuous_volumes_and_assigns_majority_year(self) -> None:
        from collectors.history import discover_crossref_issues

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "message": {
                        "items": [
                            {
                                "volume": "172",
                                "published-print": {"date-parts": [[2025, 3]]},
                            },
                            {
                                "volume": "172",
                                "published-print": {"date-parts": [[2025, 6]]},
                            },
                            {
                                "volume": "173",
                                "published-print": {"date-parts": [[2024, 12]]},
                            },
                            {
                                "volume": "173",
                                "published-print": {"date-parts": [[2024, 12]]},
                            },
                            {
                                "volume": "173",
                                "published-print": {"date-parts": [[2025, 2]]},
                            },
                            {
                                "volume": "173",
                                "published-print": {"date-parts": [[2025, 2]]},
                            },
                            {
                                "volume": "173",
                                "published-print": {"date-parts": [[2025, 2]]},
                            },
                            {
                                "volume": "999",
                                "published-print": {"date-parts": [[2027, 1]]},
                            },
                        ],
                        "next-cursor": "",
                    }
                }

        class Session:
            def get(self, url: str, **kwargs) -> Response:
                return Response()

        issues = discover_crossref_issues(
            "JDE",
            "0304-3878",
            "https://www.sciencedirect.com/journal/journal-of-development-economics/vol/{volume}/suppl/{issue}",
            years=[2025],
            session=Session(),
        )
        self.assertEqual(["jde-172-c", "jde-173-c"], [item.issue_id for item in issues])
        self.assertEqual(2025, issues[0].year)
        self.assertEqual("c", issues[0].issue)
        self.assertEqual(
            "https://www.sciencedirect.com/journal/journal-of-development-economics/vol/172/suppl/C",
            issues[0].official_url,
        )



    def test_crossref_pagination_stops_on_empty_page(self) -> None:
        from collectors.history import discover_crossref_issues

        pages = [
            {
                "message": {
                    "items": [
                        {"volume": "172", "published-print": {"date-parts": [[2025, 3]]}},
                    ],
                    "next-cursor": "abc",
                }
            },
            {
                "message": {
                    "items": [],
                    "next-cursor": "abc",
                }
            },
        ]

        class Session:
            def get(self, url: str, **kwargs) -> object:
                page = pages.pop(0)

                class Response:
                    def raise_for_status(self) -> None:
                        return None

                    def json(self) -> dict:
                        return page

                return Response()

        issues = discover_crossref_issues(
            "JDE",
            "0304-3878",
            "https://www.sciencedirect.com/journal/journal-of-development-economics/vol/{volume}/suppl/{issue}",
            years=[2025],
            session=Session(),
        )
        self.assertEqual(["jde-172-c"], [item.issue_id for item in issues])


if __name__ == "__main__":
    unittest.main()

