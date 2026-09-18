from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.apply_elsevier_expected_set_observation import build_state
from scripts.build_completeness_ledger import build_ledger


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "data/provenance/expected-set-observations/elsevier-2026-browser.json"
CONFIG_PATH = ROOT / "config/journals.yml"


class ElsevierBrowserExpectedSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        cls.config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["journals"]
        cls.state = build_state(cls.evidence, cls.config)

    def test_observation_builds_authoritative_2026_only_shard(self) -> None:
        self.assertEqual(20, len(self.state["discovery"]))
        self.assertEqual("2026-09-18T15:37:00+00:00", self.state["updated_at"])
        for journal, snapshot in self.state["discovery"].items():
            with self.subTest(journal=journal):
                self.assertEqual("official_archive_snapshot", snapshot["authority"])
                self.assertEqual(
                    "data/provenance/expected-set-observations/elsevier-2026-browser.json",
                    snapshot["evidence_ref"],
                )
                self.assertTrue(snapshot["source_url"].endswith("/issues"))
                self.assertTrue(snapshot["transport"])
                self.assertEqual(
                    {2026},
                    {int(year) for year in snapshot["issue_years"].values()},
                )

    def test_range_headers_never_synthesize_jue_155(self) -> None:
        ids = self.state["discovery"]["JUE"]["issue_ids"]
        self.assertNotIn("jue-155-c", ids)
        self.assertIn("jue-156-c", ids)
        self.assertEqual(
            "not_yet_published",
            self.state["expected_issue_exclusions"]["jue-156-c"]["status"],
        )

    def test_jce_live_issue_three_is_authoritative_expected(self) -> None:
        ids = self.state["discovery"]["JCE"]["issue_ids"]
        self.assertEqual(["jce-54-1", "jce-54-2", "jce-54-3"], ids)
        self.assertNotIn("jce-54-3", self.state["expected_issue_exclusions"])

    def test_joe_parts_preserve_publisher_identity(self) -> None:
        ids = self.state["discovery"]["JOE"]["issue_ids"]
        self.assertIn("joe-254-pa", ids)
        self.assertIn("joe-254-pb", ids)
        self.assertIn("joe-256-pa", ids)
        self.assertIn("joe-256-pb", ids)
        self.assertNotIn("joe-254-c", ids)
        self.assertNotIn("joe-256-c", ids)
        self.assertEqual(
            "not_yet_published",
            self.state["expected_issue_exclusions"]["joe-258-c"]["status"],
        )

    def test_source_identity_mismatch_fails_closed(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence["journals"]["JDE"]["source_url"] = (
            "https://www.sciencedirect.com/journal/world-development/issues"
        )
        with self.assertRaisesRegex(ValueError, "source_url"):
            build_state(evidence, self.config)

    def test_future_registration_does_not_create_missing_debt(self) -> None:
        journal = "JPubE"
        snapshot = self.state["discovery"][journal]
        current_ids = [
            issue_id
            for issue_id in snapshot["issue_ids"]
            if issue_id not in self.state["expected_issue_exclusions"]
        ]
        self.assertNotIn("jpube-262-c", current_ids)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": self.state["schema_version"],
                        "updated_at": self.state["updated_at"],
                        "expected_issue_exclusions": self.state["expected_issue_exclusions"],
                        "issues": {},
                        "discovery": {journal: snapshot},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            api_root = root / "api"
            for issue_id in current_ids:
                ref = snapshot["issue_refs"][issue_id]
                path = api_root / "journals" / "jpube" / "issues" / f"{issue_id}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "issue_id": issue_id,
                            "journal_id": "jpube",
                            "status": "ready",
                            "publication_state": "ready",
                            "content_status": "complete",
                            "source_status": "official_verified",
                        }
                    ),
                    encoding="utf-8",
                )
            ledger = build_ledger(
                state_paths=[state_path],
                journals={journal: self.config[journal]},
                api_root=api_root,
                window_start="2026-01-01",
                window_end="2026-09-18",
            )
            row = ledger["journals"][0]
            self.assertEqual("COMPLETE", row["status"])
            self.assertEqual(len(current_ids), row["expectedIssueCount"])
            self.assertEqual(1, len(row["excludedIssues"]))
            self.assertEqual("jpube-262-c", row["excludedIssues"][0]["issue_id"])


if __name__ == "__main__":
    unittest.main()
