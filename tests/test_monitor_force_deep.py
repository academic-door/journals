from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.journal_monitor import run_deep_updates


ROOT = Path(__file__).resolve().parents[1]


class ScopedForceDeepTests(unittest.TestCase):
    def test_force_deep_journals_bypass_retry_only_for_named_journal(self) -> None:
        state = {
            "journals": {
                "LUP": {
                    "candidate": {"volume": "170", "issue": ""},
                    "next_deep_retry_at": "2999-01-01T00:00:00+00:00",
                    "deep_failure_count": 2,
                },
                "WD": {
                    "candidate": {"volume": "207", "issue": ""},
                    "next_deep_retry_at": "2999-01-01T00:00:00+00:00",
                    "deep_failure_count": 34,
                },
            }
        }
        result = {"alerts": {"newly_alerting": [], "recovered": []}}
        report = {"results": [{"result": "updated"}]}
        with (
            patch(
                "scripts.journal_monitor.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as runner,
            patch("scripts.journal_monitor.read_json", return_value=report),
        ):
            failures = run_deep_updates(
                ["LUP", "WD"],
                state,
                result,
                translate=False,
                force_journals={"LUP"},
            )

        self.assertEqual(0, failures)
        runner.assert_called_once()
        command = runner.call_args.args[0]
        self.assertEqual("LUP", command[command.index("--journal") + 1])
        self.assertEqual("updated", result["deep_updates"][0]["result"])
        self.assertEqual("deferred", result["deep_updates"][1]["result"])
        self.assertIsNone(state["journals"]["LUP"]["candidate"])
        self.assertEqual(
            "2999-01-01T00:00:00+00:00",
            state["journals"]["WD"]["next_deep_retry_at"],
        )

    def test_monitor_workflow_exposes_scoped_force_input_without_broad_force(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "monitor-journals.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("force_deep_journals:", workflow)
        self.assertIn("FORCE_DEEP_JOURNALS:", workflow)
        self.assertIn("--force-deep-journal", workflow)
        self.assertNotIn("--force-deep\n", workflow)


if __name__ == "__main__":
    unittest.main()
