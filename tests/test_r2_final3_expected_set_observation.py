from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import yaml

from scripts.apply_official_expected_set_observation import build_state


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "data/provenance/expected-set-observations/final3-2026-browser.json"
CONFIG_PATH = ROOT / "config/journals.yml"


class FinalThreeExpectedSetObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        cls.config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["journals"]
        cls.state = build_state(cls.evidence, cls.config)

    def test_exact_official_entries_build_authoritative_overlay(self) -> None:
        self.assertEqual(3, len(self.state["discovery"]))
        self.assertEqual(
            13,
            sum(len(snapshot["issue_ids"]) for snapshot in self.state["discovery"].values()),
        )
        self.assertEqual(1, len(self.state["expected_issue_exclusions"]))
        for journal in ("JLE", "JAERE", "RESTAT"):
            with self.subTest(journal=journal):
                self.assertEqual(
                    "official_archive_snapshot",
                    self.state["discovery"][journal]["authority"],
                )
                self.assertEqual(
                    {2026},
                    {int(v) for v in self.state["discovery"][journal]["issue_years"].values()},
                )

    def test_jle_and_restat_do_not_infer_future_issues(self) -> None:
        self.assertEqual(
            ["jle-44-1", "jle-44-2", "jle-44-3"],
            self.state["discovery"]["JLE"]["issue_ids"],
        )
        self.assertEqual(
            ["restat-108-1", "restat-108-2", "restat-108-3", "restat-108-4"],
            self.state["discovery"]["RESTAT"]["issue_ids"],
        )

    def test_jaere_november_issue_is_explicit_but_excluded(self) -> None:
        self.assertIn("jaere-13-6", self.state["discovery"]["JAERE"]["issue_ids"])
        self.assertEqual(
            "not_yet_published",
            self.state["expected_issue_exclusions"]["jaere-13-6"]["status"],
        )
        self.assertNotIn("jaere-13-5", self.state["expected_issue_exclusions"])

    def test_entry_cannot_escape_configured_official_host(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence["journals"]["JLE"]["entries"][0]["official_url"] = (
            "https://example.com/toc/jole/2026/44/1"
        )
        with self.assertRaisesRegex(ValueError, "escaped official publisher host"):
            build_state(evidence, self.config)


if __name__ == "__main__":
    unittest.main()
