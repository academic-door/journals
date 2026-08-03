from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.provenance_ledger import (
    audit_claims,
    is_current,
    record_verification,
    sequence_digest,
)


def _claim(issue_id: str) -> dict:
    return {
        "browser_order_verification": {"pii_sequence_matched": True},
        "issue_id": issue_id,
    }


class ProvenanceLedgerTest(unittest.TestCase):
    def setUp(self):
        self.ledger = {"schema_version": "1.0", "entries": {}}

    def test_recorded_claim_passes_and_carries_auditable_fields(self):
        entry = record_verification(
            self.ledger,
            journal_id="eer",
            issue_id="eer-189-c",
            official_url="https://www.sciencedirect.com/journal/european-economic-review/vol/189/suppl/C",
            identifiers=["S0014292126000425", "S0014292126000431"],
        )
        self.assertEqual(2, entry["item_count"])
        self.assertTrue(entry["sequence_sha256"])
        self.assertTrue(entry["expires_at"])
        self.assertEqual([], audit_claims({"eer": _claim("eer-189-c")}, self.ledger))

    def test_claim_without_ledger_entry_is_reported(self):
        findings = audit_claims({"geb": _claim("geb-159-c")}, self.ledger)
        self.assertEqual(1, len(findings))
        self.assertIn("no record", findings[0])

    def test_expired_confirmation_is_reported(self):
        record_verification(
            self.ledger,
            journal_id="eer",
            issue_id="eer-189-c",
            official_url="https://example.org",
            identifiers=["a"],
            validity_days=1,
        )
        later = datetime.now(timezone.utc) + timedelta(days=3)
        findings = audit_claims({"eer": _claim("eer-189-c")}, self.ledger, now=later)
        self.assertEqual(1, len(findings))
        self.assertIn("expired", findings[0])

    def test_ledger_bound_to_the_issue_it_confirmed(self):
        record_verification(
            self.ledger,
            journal_id="eer",
            issue_id="eer-187-c",
            official_url="https://example.org",
            identifiers=["a"],
        )
        findings = audit_claims({"eer": _claim("eer-189-c")}, self.ledger)
        self.assertEqual(1, len(findings))
        self.assertIn("eer-187-c", findings[0])

    def test_journal_without_browser_claim_is_ignored(self):
        self.assertEqual([], audit_claims({"aer": {"issue_id": "aer-116-8"}}, self.ledger))

    def test_sequence_digest_is_order_sensitive(self):
        self.assertNotEqual(
            sequence_digest(["a", "b"]),
            sequence_digest(["b", "a"]),
        )

    def test_entry_without_expiry_is_not_current(self):
        self.assertFalse(is_current({"captured_at": "2026-08-03T00:00:00+00:00"}))


if __name__ == "__main__":
    unittest.main()
