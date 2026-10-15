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
ENERGY = ROOT / "data/provenance/official-rosters/sciencedirect/history/energy-163-c-2026-10-12.json"


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


    def official_case(self, key):
        source = ROOT / "tests/fixtures/publisher_coverage_official_browser_2026_10_14.json"
        cases = json.loads(source.read_text(encoding="utf-8"))["cases"]
        record = copy.deepcopy(cases[key])
        return record["publisher"], record["archive"]

    def test_official_jeem_five_missing_two_exclusions_with_verified_roster(self):
        pub, arc = self.official_case("jeem_140")
        result = reconcile(pub, arc)
        self.assertEqual("publisher_changed", result["publisher_coverage"])
        self.assertEqual([], result["verification_errors"])
        self.assertFalse(result["diagnostic_only"])
        self.assertEqual((9, 4, 2, 0), (
            result["publisher_research_count"], result["archive_research_count"],
            result["publisher_excluded_count"], result["archive_excluded_count"]))
        self.assertEqual({
            "10.1016/j.jeem.2026." + suffix
            for suffix in ("103411", "103399", "103400", "103412", "103410")
        }, set(result["diff"]["missing_research_dois"]))
        self.assertEqual({
            "10.1016/s0095-0696(26)00141-5", "10.1016/j.jeem.2026.103394"
        }, set(result["diff"]["missing_exclusion_dois"]))
        self.assertFalse(result["diff"]["research_order_mismatch"])

    def test_official_jie_eight_exact_with_order_and_board_delta(self):
        pub, arc = self.official_case("jie_163")
        result = reconcile(pub, arc)
        self.assertEqual("publisher_changed", result["publisher_coverage"])
        self.assertEqual([], result["verification_errors"])
        self.assertFalse(result["diagnostic_only"])
        self.assertEqual([], result["diff"]["missing_research_dois"])
        self.assertEqual([], result["diff"]["extra_archived_research_dois"])
        self.assertEqual(["10.1016/s0022-1996(26)00094-2"],
                         result["diff"]["missing_exclusion_dois"])
        self.assertTrue(result["diff"]["research_order_mismatch"])
        self.assertEqual(8, result["publisher_research_count"])

    def test_official_browser_provenance_and_identity_fail_closed(self):
        pub, arc = self.official_case("jie_163")
        variations = [
            ("missing timestamp", lambda p: p.pop("captured_at"), "publisher_observed_at_missing_or_invalid"),
            ("candidate DOI", lambda p: p["items"][0].update(doi_source="crossref-candidate"),
             "publisher_doi_source_unqualified"),
            ("missing DOI evidence", lambda p: p["items"][0].pop("doi_evidence_ref"),
             "publisher_doi_provenance_missing"),
            ("untrusted article url", lambda p: p["items"][0].update(
                official_article_url="https://example.com/science/article/pii/S0022199625001606"),
             "publisher_article_source_invalid"),
            ("contradictory subtype", lambda p: p["items"][0].update(
                publisher_article_type="editorial"), "publisher_publishable_type_unclassified"),
            ("bad excluded subtype", lambda p: p["excluded_items"][0].update(
                article_type="research-article"), "publisher_excluded_type_unclassified"),
        ]
        for title, mutate, expected in variations:
            with self.subTest(title=title):
                payload = copy.deepcopy(pub)
                mutate(payload)
                result = reconcile(payload, arc)
                self.assertEqual("verification_blocked", result["publisher_coverage"])
                self.assertIn(expected, result["verification_errors"])

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


    def test_archived_broad_type_mismatches_are_diagnostic_deltas(self):
        publisher, archive = self.energy_case()
        archive["articles"][0]["article_type"] = "editorial"
        archive["quality"]["excluded_items"][0]["article_type"] = "research-article"
        result = reconcile(publisher, archive)
        self.assertEqual("publisher_changed", result["publisher_coverage"])
        self.assertEqual([publisher["items"][0]["doi"]],
                         result["diff"]["archive_research_type_mismatch_dois"])
        self.assertEqual([publisher["excluded_items"][0]["doi"]],
                         result["diff"]["archive_excluded_type_mismatch_dois"])

    def test_url_volume_mismatch_fails_closed(self):
        pub, archive = self.energy_case()
        pub["official_url"] = ("https://www.sciencedirect.com/journal/"
                               "energy-economics/vol/999/suppl/C")
        result = reconcile(pub, archive)
        self.assertEqual("verification_blocked", result["publisher_coverage"])
        self.assertIn("official_url_volume_issue_mismatch",
                      result["verification_errors"])


    def test_empty_roster_and_unrelated_journal_url_fail_closed(self):
        publisher, archive = self.energy_case()
        empty = copy.deepcopy(publisher)
        empty["items"], empty["excluded_items"], empty["excluded_item_count"] = [], [], 0
        result = reconcile(empty, archive)
        self.assertEqual("verification_blocked", result["publisher_coverage"])
        self.assertIn("publisher_empty_roster_unverifiable", result["verification_errors"])
        misplaced = copy.deepcopy(publisher)
        misplaced["official_url"] = ("https://www.sciencedirect.com/journal/"
                                     "journal-of-international-economics/vol/163/suppl/C")
        result = reconcile(misplaced, archive)
        self.assertIn("official_url_journal_mismatch", result["verification_errors"])

    def test_archive_pii_conflict_fails_closed(self):
        publisher, archive = self.energy_case()
        archive["articles"][0]["source_url"] = publisher["items"][1]["official_article_url"]
        result = reconcile(publisher, archive)
        self.assertIn("publisher_archive_pii_conflict", result["verification_errors"])
        self.assertEqual("verification_blocked", result["publisher_coverage"])


if __name__ == "__main__":
    unittest.main()
