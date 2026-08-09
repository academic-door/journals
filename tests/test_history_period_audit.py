import unittest

from scripts.audit_public_data import audit_history_periods, issue_period_ordinal


class HistoryPeriodAuditTest(unittest.TestCase):
    def test_accepts_months_and_official_seasons(self) -> None:
        self.assertIsNotNone(issue_period_ordinal("January 2025"))
        self.assertIsNotNone(issue_period_ordinal("2026-08"))
        self.assertIsNotNone(issue_period_ordinal("2026年8月"))
        self.assertIsNotNone(issue_period_ordinal("Summer 2026"))

    def test_rejects_year_only_history_dates(self) -> None:
        self.assertIsNone(issue_period_ordinal("2025"))
        findings = audit_history_periods(
            "ere",
            [
                {
                    "issue_id": "ere-88-1",
                    "volume": "88",
                    "issue": "1",
                    "publication_date": "2025",
                }
            ],
        )
        self.assertEqual(1, len(findings))
        self.assertIn("requires a month or official season", findings[0])

    def test_rejects_continuous_volume_month_regression(self) -> None:
        findings = audit_history_periods(
            "eer",
            [
                {"issue_id": "eer-184-c", "volume": "184", "issue": "C", "publication_date": "April 2026"},
                {"issue_id": "eer-185-c", "volume": "185", "issue": "C", "publication_date": "February 2026"},
            ],
        )
        self.assertEqual(1, len(findings))
        self.assertIn("precedes eer-184-c", findings[0])

    def test_rejects_numbered_issue_month_regression(self) -> None:
        findings = audit_history_periods(
            "ere",
            [
                {"issue_id": "ere-89-7", "volume": "89", "issue": "7", "publication_date": "July 2026"},
                {"issue_id": "ere-89-8", "volume": "89", "issue": "8", "publication_date": "April 2026"},
            ],
        )
        self.assertEqual(1, len(findings))
        self.assertIn("precedes ere-89-7", findings[0])


if __name__ == "__main__":
    unittest.main()
