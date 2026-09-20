from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.import_official_roster_evidence import validate_evidence


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "jaere-10-1": [
        9,
        2
    ],
    "jaere-10-2": [
        8,
        1
    ],
    "jaere-10-3": [
        8,
        1
    ],
    "jaere-10-4": [
        8,
        1
    ],
    "jaere-10-5": [
        7,
        1
    ],
    "jaere-10-6": [
        8,
        1
    ],
    "jaere-11-1": [
        7,
        2
    ],
    "jaere-11-2": [
        7,
        1
    ],
    "jaere-11-3": [
        8,
        1
    ],
    "jaere-11-4": [
        8,
        1
    ],
    "jaere-11-5": [
        8,
        1
    ],
    "jaere-11-6": [
        8,
        1
    ],
    "jaere-11-S1": [
        9,
        1
    ],
    "jle-41-1": [
        8,
        1
    ],
    "jle-41-2": [
        8,
        1
    ],
    "jle-41-3": [
        8,
        3
    ],
    "jle-41-4": [
        8,
        3
    ],
    "jle-41-S1": [
        8,
        2
    ],
    "jle-42-1": [
        8,
        1
    ],
    "jle-42-2": [
        9,
        1
    ],
    "jle-42-3": [
        8,
        3
    ],
    "jle-42-4": [
        8,
        5
    ],
    "jle-42-S1": [
        11,
        2
    ],
    "jle-43-S1": [
        12,
        2
    ]
}


class ChicagoHistoricalRosterEvidenceTests(unittest.TestCase):
    def test_browser_authorized_chicago_rosters_are_complete_and_valid(self) -> None:
        found: set[str] = set()
        for issue_id, (research_count, excluded_count) in EXPECTED.items():
            journal = issue_id.split("-", 1)[0]
            path = (
                ROOT
                / "data"
                / "provenance"
                / "official-rosters"
                / journal
                / f"{issue_id}.json"
            )
            self.assertTrue(path.exists(), issue_id)
            payload = json.loads(path.read_text(encoding="utf-8"))
            validate_evidence(payload)
            self.assertEqual("browser-authorized", payload["method"])
            self.assertTrue(
                str(payload.get("capture_reference", "")).startswith("browser-use:")
            )
            self.assertEqual(research_count, len(payload["items"]), issue_id)
            self.assertEqual(
                excluded_count,
                len(payload.get("excluded_items", [])),
                issue_id,
            )
            found.add(issue_id)
        self.assertEqual(set(EXPECTED), found)
        self.assertNotIn("jaere-13-6", found)


if __name__ == "__main__":
    unittest.main()
