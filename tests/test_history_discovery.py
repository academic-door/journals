from __future__ import annotations

import unittest

from collectors.history import discover_official_issues, parse_archive


class HistoryDiscoveryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
