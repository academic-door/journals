from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.capture_wiley_roster_evidence import _eligible_record
from scripts.import_official_roster_evidence import validate_evidence


class Ier64RemainingOfficialRosterEvidenceTests(unittest.TestCase):
    def evidence(self, issue_id: str) -> dict:
        root = Path(__file__).resolve().parents[1]
        return json.loads(
            (
                root
                / "data"
                / "provenance"
                / "official-rosters"
                / "wiley"
                / f"{issue_id}.json"
            ).read_text(encoding="utf-8")
        )

    def test_ier_64_2_official_wiley_taxonomy(self) -> None:
        evidence = self.evidence("ier-64-2")
        validate_evidence(evidence)
        self.assertEqual("official-page-read", evidence["method"])
        self.assertEqual("May 2023", evidence["publication_date"])
        self.assertEqual(14, len(evidence["items"]))
        self.assertEqual(
            [
                ("10.1111/iere.12581", "issue-information"),
                ("10.1111/iere.12629", "correction"),
            ],
            [(item["doi"], item["reason"]) for item in evidence["excluded_items"]],
        )
        self.assertEqual(
            "10.1111/iere.12632",
            evidence["items"][0]["doi"],
        )
        self.assertEqual(
            "10.1111/iere.12615",
            evidence["items"][-1]["doi"],
        )

    def test_ier_64_3_official_wiley_taxonomy(self) -> None:
        evidence = self.evidence("ier-64-3")
        validate_evidence(evidence)
        self.assertEqual("official-page-read", evidence["method"])
        self.assertEqual("August 2023", evidence["publication_date"])
        self.assertEqual(13, len(evidence["items"]))
        self.assertEqual(
            [("10.1111/iere.12582", "issue-information")],
            [(item["doi"], item["reason"]) for item in evidence["excluded_items"]],
        )
        self.assertEqual(
            "10.1111/iere.12618",
            evidence["items"][0]["doi"],
        )
        self.assertEqual(
            "10.1111/iere.12624",
            evidence["items"][-1]["doi"],
        )

    def test_provisional_crossref_records_remain_ineligible_for_automatic_wiley_capture(self) -> None:
        config = {"publisher": "Wiley"}
        for issue_id in ("ier-64-2", "ier-64-3"):
            with self.subTest(issue_id=issue_id):
                self.assertFalse(
                    _eligible_record(
                        {
                            "issue_id": issue_id,
                            "journal": "IER",
                            "category": "source_pending",
                            "authority": "crossref-provisional",
                        },
                        config,
                    )
                )


if __name__ == "__main__":
    unittest.main()
