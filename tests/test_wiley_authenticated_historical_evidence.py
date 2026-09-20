from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from scripts.import_official_roster_evidence import validate_evidence

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
  "ajae-107-1": [
    14,
    1
  ],
  "ajae-107-2": [
    14,
    9
  ],
  "ajae-107-3": [
    10,
    1
  ],
  "ajae-107-4": [
    9,
    2
  ],
  "ajae-107-5": [
    9,
    1
  ],
  "jf-80-1": [
    13,
    3
  ],
  "jf-80-2": [
    15,
    5
  ],
  "jf-80-3": [
    12,
    4
  ],
  "jf-80-4": [
    12,
    3
  ],
  "jf-80-5": [
    14,
    4
  ],
  "jf-80-6": [
    16,
    3
  ]
}

class WileyAuthenticatedHistoricalEvidenceTests(unittest.TestCase):
    def test_authenticated_browser_rosters_validate_and_match_counts(self) -> None:
        for issue_id, expected in EXPECTED.items():
            path = ROOT / "data" / "provenance" / "official-rosters" / "wiley" / f"{issue_id}.json"
            self.assertTrue(path.exists(), issue_id)
            payload = json.loads(path.read_text(encoding="utf-8"))
            validate_evidence(payload)
            self.assertEqual("browser-authorized", payload["method"], issue_id)
            self.assertEqual("onlinelibrary.wiley.com", urlparse(payload["official_url"]).hostname, issue_id)
            self.assertEqual(expected[0], len(payload["items"]), issue_id)
            self.assertEqual(expected[1], len(payload.get("excluded_items", [])), issue_id)

if __name__ == "__main__":
    unittest.main()
