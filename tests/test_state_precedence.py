from __future__ import annotations

import unittest

from scripts.backfill_status import discovery_expectations


class StatePrecedenceRegressionTests(unittest.TestCase):
    def _snapshot(self, *, authority: str, refreshed_at: str, official_url: str) -> dict:
        return {
            "issues": {
                "ere-85-3-4": {
                    "journal": "ERE",
                    "year": 2023,
                    "volume": "85",
                    "issue": "3-4",
                    "official_url": official_url,
                    "status": "ready",
                    "content_status": "complete",
                    "source_status": "official_verified",
                    "publication_state": "ready",
                }
            },
            "discovery": {
                "ERE": {
                    "issue_ids": ["ere-85-3-4"],
                    "issue_years": {"ere-85-3-4": 2023},
                    "issue_refs": {
                        "ere-85-3-4": {
                            "journal": "ERE",
                            "year": 2023,
                            "volume": "85",
                            "issue": "3-4",
                            "official_url": official_url,
                        }
                    },
                    "authority": authority,
                    "refreshed_at": refreshed_at,
                    "collector_revision": "history-integrity-2026-08-11",
                }
            },
        }

    def test_authoritative_ere_observation_wins_regardless_of_shard_order(self) -> None:
        exact_url = "https://link.springer.com/journal/10640/volumes-and-issues/85-3"
        archive_root = "https://link.springer.com/journal/10640/volumes-and-issues"
        authoritative = self._snapshot(
            authority="official_archive",
            refreshed_at="2026-08-24T17:00:00+00:00",
            official_url=exact_url,
        )
        newer_candidate = self._snapshot(
            authority="crossref_candidate",
            refreshed_at="2026-09-10T20:00:00+00:00",
            official_url=archive_root,
        )

        for states in ([authoritative, newer_candidate], [newer_candidate, authoritative]):
            merged_issues = {
                issue_id: dict(entry) for issue_id, entry in authoritative["issues"].items()
            }
            with self.subTest(order=[s["discovery"]["ERE"]["authority"] for s in states]):
                expectation = discovery_expectations(states, merged_issues)["ere-85-3-4"]
                self.assertEqual("official_archive", expectation["authority"])
                self.assertEqual(exact_url, expectation["official_url"])

    def test_exact_issue_reference_beats_collection_root_with_same_authority(self) -> None:
        exact_url = "https://link.springer.com/journal/10640/volumes-and-issues/85-3"
        archive_root = "https://link.springer.com/journal/10640/volumes-and-issues"
        exact = self._snapshot(
            authority="official_archive",
            refreshed_at="2026-09-10T18:00:00+00:00",
            official_url=exact_url,
        )
        newer_root = self._snapshot(
            authority="official_archive",
            refreshed_at="2026-09-10T19:00:00+00:00",
            official_url=archive_root,
        )

        expectation = discovery_expectations(
            [exact, newer_root],
            {issue_id: dict(entry) for issue_id, entry in exact["issues"].items()},
        )["ere-85-3-4"]
        self.assertEqual(exact_url, expectation["official_url"])

    def test_discovery_precedence_does_not_rewrite_operational_lifecycle_truth(self) -> None:
        exact_url = "https://link.springer.com/journal/10640/volumes-and-issues/85-3"
        archive_root = "https://link.springer.com/journal/10640/volumes-and-issues"
        authoritative = self._snapshot(
            authority="official_archive",
            refreshed_at="2026-09-10T18:11:14+00:00",
            official_url=exact_url,
        )
        stale_candidate = self._snapshot(
            authority="crossref_candidate",
            refreshed_at="2026-08-24T17:03:09+00:00",
            official_url=archive_root,
        )
        stale_candidate["issues"]["ere-85-3-4"].update(
            {
                "status": "blocked",
                "content_status": "blocked",
                "source_status": "source_pending",
                "publication_state": "blocked",
            }
        )
        merged_issues = {
            "ere-85-3-4": dict(stale_candidate["issues"]["ere-85-3-4"])
        }

        expectation = discovery_expectations(
            [authoritative, stale_candidate], merged_issues
        )["ere-85-3-4"]

        self.assertEqual("official_archive", expectation["authority"])
        self.assertEqual(exact_url, expectation["official_url"])
        self.assertEqual(exact_url, merged_issues["ere-85-3-4"]["official_url"])
        self.assertEqual("blocked", merged_issues["ere-85-3-4"]["publication_state"])
        self.assertEqual("blocked", merged_issues["ere-85-3-4"]["content_status"])
        self.assertEqual("source_pending", merged_issues["ere-85-3-4"]["source_status"])


if __name__ == "__main__":
    unittest.main()
