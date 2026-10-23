"""Ensure production publishers use identical durable history-state inputs."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _history_publish_steps(workflow_name: str) -> list[str]:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
    )
    scripts: list[str] = []
    for job in workflow.get("jobs", {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []):
            command = str(step.get("run", "")) if isinstance(step, dict) else ""
            if "python scripts/backfill_status.py" in command:
                scripts.append(command)
    return scripts


class HistoryProducerStateScopeTests(unittest.TestCase):
    def test_monitor_and_targeted_update_use_same_full_state_shards(self) -> None:
        shard_glob = 'for state in "$data_tree"/data/backfill-state/*.json; do'
        for workflow_name in ("monitor-journals.yml", "update-journals.yml"):
            with self.subTest(workflow=workflow_name):
                scripts = _history_publish_steps(workflow_name)
                self.assertEqual(1, len(scripts), "missing/duplicate history producer")
                script = scripts[0]
                self.assertIn(shard_glob, script)
                self.assertIn('state_args+=(--state "$state")', script)
                self.assertIn("python scripts/audit_history_coverage.py", script)

    def test_both_history_producers_publish_the_gap_manifest(self) -> None:
        """Backfill coverage and gap manifest must share the same state scope."""
        for workflow_name in ("monitor-journals.yml", "update-journals.yml"):
            with self.subTest(workflow=workflow_name):
                script = _history_publish_steps(workflow_name)[0]
                self.assertIn("python scripts/build_gap_manifest.py", script)
                self.assertIn("--out-json public/api/v1/gap-manifest.json", script)
                self.assertIn("--out-md public/gap-manifest.md", script)
                self.assertIn(
                    '"$data_tree/public/gap-manifest.md"',
                    script,
                )
                self.assertIn(
                    'git -C "$data_tree" add public/gap-manifest.md',
                    script,
                )

    def test_targeted_update_does_not_restrict_history_to_two_periods(self) -> None:
        script = _history_publish_steps("update-journals.yml")[0]
        self.assertNotIn("data/backfill-state/field-2023-2024.json \\", script)
        self.assertNotIn("data/backfill-state/field-2025-2026.json; do", script)


if __name__ == "__main__":
    unittest.main()
