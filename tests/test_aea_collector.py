from __future__ import annotations

import unittest

from collectors.aea import NON_RESEARCH_PATTERN


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


if __name__ == "__main__":
    unittest.main()
