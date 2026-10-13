"""Publisher coverage #426: real counterexamples and fail-closed regressions.

JEEM/JIE issue-derived fixtures lack complete first-party PII/title/timestamp
and therefore MUST remain diagnostic-only verification_blocked. ENERGY uses
the immutable first-party official roster already in the main repository.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.publisher_coverage_sentinel import reconcile

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests/fixtures/publisher_coverage_issue_diagnostics.json"
ENERGY = ROOT / "data/provenance/official-rosters/sciencedirect/energy-163-c.json"


class PublisherCoverageSentinelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(CASES.read_text(encoding="utf-8"))

    def case(self, key):
        x = copy.deepcopy(self.cases[key])
        return x["publisher"], x["archive"]

    def energy_case(self):
        publisher = json.loads(ENERGY.read_text(encoding="utf-8"))
        archive = {
            "journal_id": "energy", "issue_id": "energy-163-c",
            "articles": [{"doi": row["doi"]} for row in publisher["items"]],
            "quality": {"excluded_items": [
                {"doi": row["doi"]} for row in publisher["excluded_items"]]},
        }
        return publisher, archive

    def test_jeem_five_missing_plus_two_exclusions_diagnostic_not_verified(self):
        pub, arc = self.case("jeem_140")
        result = reconcile(pub, arc, last_verified_at="2026-08-03T08:28:00Z")
        self.assertEqual((9, 4, 2, 0), (
            result["publisher_research_count"], result["archive_research_count"],
            result["publisher_excluded_count"], result["archive_excluded_count"]))
        self.assertEqual(5, len(result["diff"]["missing_research_dois"]))
        self.assertEqual(2, len(result["diff"]["missing_exclusion_dois"]))
        self.assertEqual([], result["diff"]["extra_archived_research_dois"])
        self.assertEqual("verification_blocked", result["publisher_coverage"])
        self.assertTrue(result["diagnostic_only"])
        self.assertIsNone(result["observed_at"])
        self.assertEqual("2026-08-03T08:28:00Z", result["last_verified_at"])

    def test_jie_no_ninth_research_article_but_wrong_order_and_board(self):
        pub, arc = self.case("jie_163")
        result = reconcile(pub, arc)
        self.assertEqual((8, 8, 2, 1), (
            result["publisher_research_count"], result["archive_research_count"],
            result["publisher_excluded_count"], result["archive_excluded_count"]))
        self.assertEqual([], result["diff"]["missing_research_dois"])
        self.assertEqual([], result["diff"]["extra_archived_research_dois"])
        self.assertTrue(result["diff"]["research_order_mismatch"])
        self.assertEqual(["10.1016/s0022-1996(26)00094-2"],
                         result["diff"]["missing_exclusion_dois"])
        self.assertNotIn("10.1016/j.jinteco.2026.104295",
                         result["diff"]["missing_research_dois"])
        self.assertEqual("verification_blocked", result["publisher_coverage"])

    def test_energy_exact_thirty_three_plus_one_correction(self):
        publisher, archive = self.energy_case()
        result = reconcile(publisher, archive)
        self.assertEqual("reconciled_as_of", result["publisher_coverage"])
        self.assertEqual((33, 1), (result["publisher_research_count"],
                                   result["publisher_excluded_count"]))
        self.assertFalse(result["diff"]["research_order_mismatch"])
        self.assertEqual([], result["verification_errors"])

    def test_no_change_does_not_refetch_and_fingerprint_ignores_timestamp(self):
        publisher, archive = self.energy_case()
        first = reconcile(publisher, archive)
        publisher["captured_at"] = "2026-10-13T10:10:00Z"
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            second = reconcile(publisher, archive)
        self.assertEqual(first["publisher_fingerprint_sha256"],
                         second["publisher_fingerprint_sha256"])
        self.assertEqual("reconciled_as_of", second["publisher_coverage"])

    def test_http_403_and_timeout_never_set_reconciled(self):
        pub, arc = self.case("jie_163")
        for failure in ("HTTP 403", "timeout"):
            with self.subTest(failure=failure):
                blocked = {"journal_id": "jie", "issue_id": "jie-163-c",
                           "official_url": pub["official_url"],
                           "method": "browser-authorized",
                           "source_error": failure}
                result = reconcile(blocked, arc,
                                   last_verified_at="2026-09-01T00:00:00Z")
                self.assertEqual("verification_blocked", result["publisher_coverage"])
                self.assertIsNone(result["diff"])
                self.assertIsNone(result["observed_at"])
                self.assertEqual("2026-09-01T00:00:00Z",
                                 result["last_verified_at"])

    def test_missing_and_duplicate_identity_fail_closed(self):
        publisher, archive = self.energy_case()
        for bad_field, error in (
            ("doi", "publisher_duplicate_doi"),
            ("source_id", "publisher_pii_missing_or_conflicting"),
        ):
            with self.subTest(bad_field=bad_field):
                bad = copy.deepcopy(publisher)
                bad["items"][1][bad_field] = bad["items"][0][bad_field]
                result = reconcile(bad, archive)
                self.assertEqual("verification_blocked", result["publisher_coverage"])
                self.assertIn(error, result["verification_errors"])
        bad = copy.deepcopy(publisher)
        bad["items"][0].pop("doi")
        self.assertIn("publisher_doi_missing_or_invalid",
                      reconcile(bad, archive)["verification_errors"])

    def test_withdrawal_and_type_reclassification_are_not_new_research(self):
        pub, archive = self.energy_case()
        withdrawn = copy.deepcopy(pub)
        removed = withdrawn["items"].pop(0)
        result = reconcile(withdrawn, archive)
        self.assertEqual("publisher_changed", result["publisher_coverage"])
        self.assertEqual([removed["doi"]],
                         result["diff"]["extra_archived_research_dois"])
        corrected = copy.deepcopy(pub)
        moved = corrected["items"].pop(0)
        moved["article_type"], moved["reason"] = "editorial", "official-editorial"
        corrected["excluded_items"].append(moved)
        corrected["excluded_items"].sort(key=lambda row: row["source_sequence"])
        corrected["excluded_item_count"] += 1
        result = reconcile(corrected, archive)
        self.assertEqual("publisher_changed", result["publisher_coverage"])
        self.assertEqual([moved["doi"]],
                         result["diff"]["nonresearch_misclassified_as_research"])

    def test_publisher_subtype_mismatch_preserves_research_taxonomy(self):
        pub, archive = self.energy_case()
        pub["items"][0]["publisher_article_type"] = "review article"
        archive["articles"][0]["publisher_article_type"] = "research article"
        result = reconcile(pub, archive)
        self.assertEqual("publisher_changed", result["publisher_coverage"])
        self.assertEqual([pub["items"][0]["doi"]],
                         result["diff"]["publisher_subtype_mismatch_dois"])

    def test_url_volume_mismatch_fails_closed(self):
        pub, archive = self.energy_case()
        pub["official_url"] = ("https://www.sciencedirect.com/journal/"
                               "energy-economics/vol/999/suppl/C")
        result = reconcile(pub, archive)
        self.assertEqual("verification_blocked", result["publisher_coverage"])
        self.assertIn("official_url_volume_issue_mismatch",
                      result["verification_errors"])


if __name__ == "__main__":
    unittest.main()
