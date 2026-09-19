from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.import_official_roster_evidence import validate_evidence


ROOT = Path(__file__).resolve().parents[1]
ROSTER_ROOT = ROOT / "data/provenance/official-rosters"

EXPECTED = {
    "jaere/jaere-13-5.json": (8, 1),
    "restat/restat-108-1.json": (19, 0),
    "restat/restat-108-2.json": (18, 0),
    "restat/restat-108-3.json": (21, 1),
    "restat/restat-108-4.json": (21, 1),
}

RESTAT_RESTORED = {
    "10.1162/rest_a_01413",
    "10.1162/rest_a_01399",
    "10.1162/rest_a_01384",
    "10.1162/rest_a_01371",
    "10.1162/rest_a_01400",
    "10.1162/rest_a_01391",
    "10.1162/rest_a_01380",
    "10.1162/rest_a_01358",
    "10.1162/rest_a_01452",
    "10.1162/rest_a_01458",
    "10.1162/rest_a_01439",
    "10.1162/rest_a_01390",
}


class FinalR2RosterEvidenceTests(unittest.TestCase):
    def load(self, relative: str) -> dict:
        return json.loads((ROSTER_ROOT / relative).read_text(encoding="utf-8"))

    def test_final_rosters_pass_shared_evidence_gate(self) -> None:
        for relative, (item_count, excluded_count) in EXPECTED.items():
            with self.subTest(relative=relative):
                evidence = self.load(relative)
                validate_evidence(evidence)
                self.assertEqual(item_count, len(evidence["items"]))
                self.assertEqual(excluded_count, len(evidence.get("excluded_items", [])))
                self.assertEqual(
                    list(range(1, item_count + 1)),
                    [item["sequence"] for item in evidence["items"]],
                )
                self.assertTrue(evidence.get("capture_reference"))
                self.assertTrue(all(item["title_en"].strip() for item in evidence["items"]))

    def test_restat_restoration_targets_have_official_article_urls(self) -> None:
        seen: set[str] = set()
        for relative in EXPECTED:
            if not relative.startswith("restat/"):
                continue
            evidence = self.load(relative)
            for item in evidence["items"]:
                doi = item["doi"].casefold()
                if doi not in RESTAT_RESTORED:
                    continue
                seen.add(doi)
                self.assertTrue(
                    item.get("official_article_url", "").startswith(
                        "https://direct.mit.edu/rest/article/"
                    ),
                    doi,
                )
        self.assertEqual(RESTAT_RESTORED, seen)

    def test_publisher_nonresearch_items_are_explicitly_excluded(self) -> None:
        jaere = self.load("jaere/jaere-13-5.json")
        self.assertEqual(
            ["10.1086/743613"],
            [item["doi"] for item in jaere["excluded_items"]],
        )
        restat3 = self.load("restat/restat-108-3.json")
        self.assertEqual(
            ["10.1162/rest.x.1676"],
            [item["doi"] for item in restat3["excluded_items"]],
        )
        restat4 = self.load("restat/restat-108-4.json")
        self.assertEqual(
            ["10.1162/rest.e.1731"],
            [item["doi"] for item in restat4["excluded_items"]],
        )


if __name__ == "__main__":
    unittest.main()
