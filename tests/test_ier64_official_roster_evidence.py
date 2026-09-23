from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.capture_wiley_roster_evidence import _eligible_record
from scripts.import_official_roster_evidence import validate_evidence


class Ier64OfficialRosterEvidenceTests(unittest.TestCase):
    def test_ier_64_1_official_wiley_roster_excludes_non_articles(self) -> None:
        root = Path(__file__).resolve().parents[1]
        evidence = json.loads(
            (
                root
                / "data"
                / "provenance"
                / "official-rosters"
                / "wiley"
                / "ier-64-1.json"
            ).read_text(encoding="utf-8")
        )

        validate_evidence(evidence)
        self.assertEqual("official-page-read", evidence["method"])
        self.assertEqual(
            "https://onlinelibrary.wiley.com/toc/14682354/2023/64/1",
            evidence["official_url"],
        )
        self.assertEqual(14, len(evidence["items"]))
        excluded = {item["doi"]: item for item in evidence["excluded_items"]}
        self.assertEqual("issue-information", excluded["10.1111/iere.12580"]["reason"])
        self.assertEqual(
            "list-of-reviewers",
            excluded["10.1111/iere.12628"]["reason"],
        )
        self.assertNotIn(
            "10.1111/iere.12628",
            {item["doi"] for item in evidence["items"]},
        )

    def test_crossref_provisional_record_still_cannot_self_promote_via_capture(self) -> None:
        record = {
            "issue_id": "ier-64-1",
            "journal": "IER",
            "category": "recoverable",
            "authority": "crossref-provisional",
        }
        config = {"publisher": "Wiley"}
        self.assertFalse(_eligible_record(record, config))


if __name__ == "__main__":
    unittest.main()
