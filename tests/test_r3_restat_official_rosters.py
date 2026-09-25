from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.import_official_roster_evidence import validate_evidence


ROOT = Path(__file__).resolve().parents[1]
ROSTER_ROOT = ROOT / "data/provenance/official-rosters/restat"
MANIFEST = ROOT / "data/provenance/official-evidence-batches/restat-r3-2023-2025.json"

EXPECTED = {
    "restat-105-5": (19, {"10.1162/rest_e_01347"}),
    "restat-105-6": (20, set()),
    "restat-106-1": (19, set()),
    "restat-106-2": (20, set()),
    "restat-106-4": (18, set()),
    "restat-106-5": (17, set()),
    "restat-106-6": (19, set()),
    "restat-107-1": (20, set()),
    "restat-107-2": (19, set()),
    "restat-107-3": (20, set()),
    "restat-107-4": (21, {"10.1162/rest_e_01583"}),
    "restat-107-5": (20, {"10.1162/rest.x.277"}),
    "restat-107-6": (20, set()),
}


class RestatR3OfficialRosterTests(unittest.TestCase):
    def load(self, issue_id: str) -> dict:
        return json.loads((ROSTER_ROOT / f"{issue_id}.json").read_text(encoding="utf-8"))

    def test_every_roster_passes_shared_official_evidence_gate(self) -> None:
        for issue_id in EXPECTED:
            with self.subTest(issue_id=issue_id):
                evidence = self.load(issue_id)
                validate_evidence(evidence)
                self.assertEqual("official-page-read", evidence["method"])
                self.assertEqual(
                    f"https://direct.mit.edu/rest/issue/{issue_id.split('-')[1]}/{issue_id.split('-')[2]}",
                    evidence["official_url"],
                )
                self.assertTrue(
                    evidence["capture_reference"].startswith("parallel-search-extract:")
                )

    def test_exact_publishable_counts_order_and_exclusions(self) -> None:
        total_publishable = 0
        total_excluded = 0
        for issue_id, (expected_count, expected_excluded) in EXPECTED.items():
            with self.subTest(issue_id=issue_id):
                evidence = self.load(issue_id)
                self.assertEqual(expected_count, len(evidence["items"]))
                self.assertEqual(
                    list(range(1, expected_count + 1)),
                    [item["sequence"] for item in evidence["items"]],
                )
                excluded = {
                    str(item.get("doi", "")).casefold()
                    for item in evidence.get("excluded_items", [])
                }
                self.assertEqual(expected_excluded, excluded)
                self.assertEqual(len(excluded), evidence["excluded_item_count"])
                total_publishable += expected_count
                total_excluded += len(excluded)
        self.assertEqual(252, total_publishable)
        self.assertEqual(3, total_excluded)

    def test_batch_manifest_is_exact_and_finalized(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual("restat-r3-2023-2025", manifest["batch_id"])
        self.assertEqual("mit-press-direct", manifest["publisher_family"])
        self.assertEqual("official-page-read", manifest["method"])
        self.assertTrue(manifest["finalized"])
        self.assertEqual(list(EXPECTED), manifest["issue_ids"])
        self.assertEqual(
            [f"data/provenance/official-rosters/restat/{issue_id}.json" for issue_id in EXPECTED],
            manifest["evidence_paths"],
        )


if __name__ == "__main__":
    unittest.main()
