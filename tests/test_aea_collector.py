from __future__ import annotations

import unittest

from collectors.aea import ISSUE_PERIOD_PATTERN, NON_RESEARCH_PATTERN


class AEACollectorTests(unittest.TestCase):
    def test_excludes_audit_and_annual_reports(self) -> None:
        self.assertRegex("Report of Independent Auditor", NON_RESEARCH_PATTERN)
        self.assertRegex("Report of the Independent Auditor", NON_RESEARCH_PATTERN)
        self.assertRegex("Annual Report 2025", NON_RESEARCH_PATTERN)
        self.assertRegex(
            "Recommendations for Further Reading", NON_RESEARCH_PATTERN
        )

    def test_keeps_research_titles_containing_report(self) -> None:
        self.assertNotRegex(
            "The Employment Effects of Mandatory ESG Reporting",
            NON_RESEARCH_PATTERN,
        )

    def test_accepts_monthly_and_seasonal_issue_periods(self) -> None:
        self.assertRegex("August 2026", ISSUE_PERIOD_PATTERN)
        self.assertRegex("Spring 2026", ISSUE_PERIOD_PATTERN)


if __name__ == "__main__":
    unittest.main()
