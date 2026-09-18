from __future__ import annotations

import copy
import unittest

from scripts import update_journals


def issue(issue_id: str, *, source_status: str, publication_state: str) -> dict:
    payload = {
        "issue_id": issue_id,
        "journal_id": "ecta",
        "volume": "94",
        "issue": "4",
        "publication_date": "July 2026",
        "content_status": "complete",
        "source_status": source_status,
        "publication_state": publication_state,
        "quality": {
            "roster_authority": (
                "repec-publisher-supplied"
                if publication_state == "ready"
                else "crossref-provisional"
            ),
            "roster_transport": (
                "repec-serial-page"
                if publication_state == "ready"
                else "crossref"
            ),
            "flags": [] if publication_state == "ready" else ["crossref_provisional_roster"],
        },
    }
    return payload


class ReadyArchiveFloorTests(unittest.TestCase):
    def test_same_issue_ready_archive_beats_regressed_current(self) -> None:
        current = issue(
            "ecta-94-4",
            source_status="source_pending",
            publication_state="source_pending",
        )
        archive = issue(
            "ecta-94-4",
            source_status="publisher_verified",
            publication_state="ready",
        )

        chosen = update_journals.prefer_ready_archive(current, archive)

        self.assertEqual("ready", chosen["publication_state"])
        self.assertEqual("publisher_verified", chosen["source_status"])
        self.assertEqual(
            "repec-publisher-supplied",
            chosen["quality"]["roster_authority"],
        )

    def test_same_issue_ready_current_is_not_replaced(self) -> None:
        current = issue(
            "ecta-94-4",
            source_status="publisher_verified",
            publication_state="ready",
        )
        current["marker"] = "current"
        archive = copy.deepcopy(current)
        archive["marker"] = "archive"

        chosen = update_journals.prefer_ready_archive(current, archive)

        self.assertEqual("current", chosen["marker"])


if __name__ == "__main__":
    unittest.main()
