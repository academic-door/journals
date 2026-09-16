from __future__ import annotations

import unittest

from scripts.audit_freshness import audit_journal_freshness


def issue(
    issue_id: str,
    volume: str,
    number: str,
    publication_date: str,
    *,
    publication_state: str = "ready",
    abstracts: int = 8,
    translations: int = 8,
) -> dict:
    return {
        "issue_id": issue_id,
        "volume": volume,
        "issue": number,
        "publication_date": publication_date,
        "publication_state": publication_state,
        "research_article_count": max(abstracts, translations),
        "quality": {
            "abstract_en_complete": abstracts,
            "translation_complete": translations,
        },
    }


class FreshnessAuditTests(unittest.TestCase):
    def codes(self, findings: list[dict]) -> set[str]:
        return {str(item.get("code", "")) for item in findings}

    def test_same_issue_period_divergence_is_flagged(self) -> None:
        ready = issue("jpe-134-8", "134", "8", "August 2026")
        detected = issue(
            "jpe-134-8",
            "134",
            "8",
            "2026-04-02T04:52:53Z",
            publication_state="blocked",
            abstracts=1,
            translations=0,
        )
        findings = audit_journal_freshness("jpe", ready, detected, {})
        self.assertIn("same_issue_period_divergence", self.codes(findings))
        self.assertIn("same_issue_detected_quality_regression", self.codes(findings))

    def test_older_detected_snapshot_is_flagged(self) -> None:
        ready = issue("jie-163-c", "163", "c", "October 2026")
        detected = issue("jie-162-c", "162", "c", "August 2026")
        findings = audit_journal_freshness("jie", ready, detected, {})
        self.assertIn("detected_older_than_ready", self.codes(findings))

    def test_genuinely_newer_detected_is_not_a_regression(self) -> None:
        ready = issue("wd-206-c", "206", "c", "October 2026")
        detected = issue(
            "wd-207-c",
            "207",
            "c",
            "November 2026",
            publication_state="blocked",
            abstracts=20,
            translations=5,
        )
        findings = audit_journal_freshness("wd", ready, detected, {})
        self.assertNotIn("detected_older_than_ready", self.codes(findings))
        self.assertNotIn("same_issue_detected_quality_regression", self.codes(findings))

    def test_monitor_candidate_ahead_of_reader_is_flagged(self) -> None:
        ready = issue("ecta-94-4", "94", "4", "July 2026")
        monitor = {
            "status": "awaiting_official",
            "candidate": {
                "issue_key": "94:5",
                "volume": "94",
                "issue": "5",
                "publication_date": "2026-09-01",
                "doi_count": 12,
            },
        }
        findings = audit_journal_freshness("ecta", ready, None, monitor)
        self.assertIn("monitor_candidate_ahead", self.codes(findings))
        finding = next(item for item in findings if item["code"] == "monitor_candidate_ahead")
        self.assertEqual(finding["severity"], "warning")
        self.assertEqual(finding["candidate_issue_key"], "94:5")

    def test_matching_ready_and_detected_is_clean(self) -> None:
        ready = issue("aer-116-9", "116", "9", "September 2026")
        detected = issue("aer-116-9", "116", "9", "September 2026")
        self.assertEqual(audit_journal_freshness("aer", ready, detected, {}), [])


if __name__ == "__main__":
    unittest.main()
