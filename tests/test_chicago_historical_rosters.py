from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.import_official_roster_evidence import validate_evidence


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "jaere-10-1",
    "jaere-10-2",
    "jaere-10-3",
    "jaere-10-4",
    "jaere-10-5",
    "jaere-10-6",
    "jaere-11-1",
    "jaere-11-2",
    "jaere-11-3",
    "jaere-11-4",
    "jaere-11-5",
    "jaere-11-6",
    "jaere-11-S1",
    "jle-41-1",
    "jle-41-2",
    "jle-41-3",
    "jle-41-4",
    "jle-41-S1",
    "jle-42-1",
    "jle-42-2",
    "jle-42-3",
    "jle-42-4",
    "jle-42-S1",
    "jle-43-S1",
}


class ChicagoHistoricalRosterEvidenceTests(unittest.TestCase):
    def test_browser_authorized_chicago_rosters_are_complete_and_valid(self) -> None:
        found: set[str] = set()
        for journal in ("jaere", "jle"):
            root = ROOT / "data" / "provenance" / "official-rosters" / journal
            for path in sorted(root.glob("*.json")):
                if path.stem not in EXPECTED:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                validate_evidence(payload)
                self.assertEqual("browser-authorized", payload["method"])
                self.assertTrue(str(payload.get("capture_reference", "")).startswith("browser-use:"))
                found.add(path.stem)
        self.assertEqual(EXPECTED, found)
        self.assertNotIn("jaere-13-6", found)


if __name__ == "__main__":
    unittest.main()
