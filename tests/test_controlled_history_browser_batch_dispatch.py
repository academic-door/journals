from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_history_browser_batch_dispatch.py"
TRIGGER_WORKFLOW = ROOT / ".github/workflows/controlled-history-trigger.yml"


class ControlledHistoryBrowserBatchDispatchTests(unittest.TestCase):
    def _event(
        self,
        *,
        actor: str = "SIMON-WORLD",
        issue_number: int = 205,
        body: str = "/history-browser-batch red-2023-2025",
        pull_request: bool = False,
    ) -> dict[str, object]:
        issue: dict[str, object] = {"number": issue_number}
        if pull_request:
            issue["pull_request"] = {"url": "https://api.github.com/repos/x/y/pulls/1"}
        return {
            "action": "created",
            "comment": {"body": body, "user": {"login": actor}},
            "issue": issue,
        }

    def _write_bundle(self, root: Path, *, include_snapshot: bool = True) -> None:
        snapshot = Path(
            "data/provenance/browser-snapshots/sciencedirect/red-47-c.json"
        )
        manifest = root / "data/provenance/browser-batches/red-2023-2025.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "batch_id": "red-2023-2025",
                    "publisher_family": "elsevier-sciencedirect",
                    "journal_id": "red",
                    "transport": "browser-authorized",
                    "policy_decision": "0018",
                    "finalized": True,
                    "issue_ids": ["red-47-c"],
                    "snapshot_paths": [str(snapshot)],
                }
            ),
            encoding="utf-8",
        )
        if include_snapshot:
            path = root / snapshot
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "journal_id": "red",
                        "issue_id": "red-47-c",
                        "official_url": (
                            "https://www.sciencedirect.com/journal/"
                            "review-of-economic-dynamics/vol/47/suppl/C"
                        ),
                        "capture_mode": "browser-authorized",
                        "items": [
                            {
                                "title": "Example",
                                "href": (
                                    "https://www.sciencedirect.com/science/"
                                    "article/pii/S109420252100079X"
                                ),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

    def _run(
        self,
        event: dict[str, object] | None = None,
        *,
        include_snapshot: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_bundle(root, include_snapshot=include_snapshot)
            event_path = root / "event.json"
            output_path = root / "github-output.txt"
            event_path.write_text(
                json.dumps(event or self._event()), encoding="utf-8"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(event_path),
                    "--repo-root",
                    str(root),
                    "--github-output",
                    str(output_path),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            output = (
                output_path.read_text(encoding="utf-8")
                if output_path.exists()
                else ""
            )
            return result, output

    def test_valid_batch_outputs_exact_issue_set(self) -> None:
        result, output = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("batch_id=red-2023-2025\\n", output)
        self.assertIn("issue_ids=red-47-c\\n", output)
        self.assertIn("issue_count=1\\n", output)

    def test_wrong_issue_actor_and_extended_command_are_rejected(self) -> None:
        cases = [
            self._event(issue_number=265),
            self._event(actor="someone-else"),
            self._event(body="/history-browser-batch red-2023-2025 extra"),
            self._event(pull_request=True),
        ]
        for event in cases:
            with self.subTest(event=event):
                result, _ = self._run(event)
                self.assertNotEqual(result.returncode, 0)

    def test_missing_snapshot_is_rejected(self) -> None:
        result, _ = self._run(include_snapshot=False)
        self.assertNotEqual(result.returncode, 0)

    def test_private_session_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_bundle(root)
            snapshot = (
                root
                / "data/provenance/browser-snapshots/sciencedirect/red-47-c.json"
            )
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            payload["cookie"] = "must-not-persist"
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            event_path = root / "event.json"
            output_path = root / "out.txt"
            event_path.write_text(json.dumps(self._event()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(event_path),
                    "--repo-root",
                    str(root),
                    "--github-output",
                    str(output_path),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_workflow_has_bounded_batch_route(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("validate_browser_batch:", workflow)
        self.assertIn("scripts/validate_history_browser_batch_dispatch.py", workflow)
        self.assertIn("github.event.issue.number == 205", workflow)
        self.assertIn("startsWith(github.event.comment.body, '/history-browser-batch ')", workflow)
        self.assertIn("browser_batch:", workflow)
        self.assertIn("needs: validate_browser_batch", workflow)
        self.assertIn("categories: browser", workflow)
        self.assertIn(
            "evidence_issue_ids: ${{ needs.validate_browser_batch.outputs.issue_ids }}",
            workflow,
        )
        self.assertIn('max_issues: "20"', workflow)


if __name__ == "__main__":
    unittest.main()
