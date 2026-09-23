from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.validate_history_browser_batch_dispatch import validate_event


class CerBrowserRosterEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def load(self, rel: str) -> dict:
        return json.loads((self.root / rel).read_text(encoding="utf-8"))

    def test_cer_snapshots_capture_full_official_card_counts(self) -> None:
        cer80 = self.load("data/provenance/browser-snapshots/sciencedirect/cer-80-c.json")
        cer94 = self.load("data/provenance/browser-snapshots/sciencedirect/cer-94-c.json")

        self.assertEqual(27, len(cer80["items"]))
        self.assertEqual(
            26,
            sum(item["type"] == "research-article" for item in cer80["items"]),
        )
        self.assertEqual("editorial", cer80["items"][0]["type"])

        self.assertEqual(13, len(cer94["items"]))
        self.assertEqual(
            11,
            sum(item["type"] == "research-article" for item in cer94["items"]),
        )
        self.assertEqual(
            ["editorial", "editorial"],
            [item["type"] for item in cer94["items"] if item["type"] != "research-article"],
        )

    def test_cer_batch_is_bounded_and_dispatch_valid(self) -> None:
        event = {
            "action": "created",
            "issue": {"number": 205},
            "comment": {
                "user": {"login": "SIMON-WORLD"},
                "body": "/history-browser-batch elsevier-r3-cer",
            },
        }
        batch_id, issue_ids = validate_event(event, repo_root=self.root)
        self.assertEqual("elsevier-r3-cer", batch_id)
        self.assertEqual(["cer-80-c", "cer-94-c"], issue_ids)

        batch = self.load("data/provenance/browser-batches/elsevier-r3-cer.json")
        self.assertTrue(batch["finalized"])
        self.assertEqual("browser-authorized", batch["transport"])
        self.assertEqual("0018", batch["policy_decision"])


if __name__ == "__main__":
    unittest.main()
