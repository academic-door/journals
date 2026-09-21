from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_history_evidence_dispatch.py"
TRIGGER_WORKFLOW = ROOT / ".github/workflows/controlled-history-trigger.yml"
HISTORY_WORKFLOW = ROOT / ".github/workflows/history-sprint.yml"


class ControlledHistoryDispatchTests(unittest.TestCase):
    def _event(
        self,
        *,
        actor: str = "SIMON-WORLD",
        issue_number: int = 265,
        body: str = "/history-evidence wd-207-c",
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

    def _run_validator(
        self,
        event: dict[str, object],
        *,
        snapshot: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            event_path = repo_root / "event.json"
            output_path = repo_root / "github-output.txt"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            if snapshot:
                snapshot_path = (
                    repo_root
                    / "data/provenance/browser-snapshots/sciencedirect/wd-207-c.json"
                )
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                snapshot_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "journal_id": "wd",
                            "issue_id": "wd-207-c",
                            "official_url": (
                                "https://www.sciencedirect.com/journal/"
                                "world-development/vol/207/suppl/C"
                            ),
                            "items": [{"title": "example"}],
                        }
                    ),
                    encoding="utf-8",
                )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(event_path),
                    "--repo-root",
                    str(repo_root),
                    "--github-output",
                    str(output_path),
                ],
                cwd=repo_root,
                text=True,
                capture_output=True,
                check=False,
            )
            output = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
            return result, output

    def test_trusted_valid_comment_selects_only_issue_id(self) -> None:
        result, output = self._run_validator(self._event())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, "issue_id=wd-207-c\n")

    def test_untrusted_actor_is_rejected(self) -> None:
        result, _ = self._run_validator(self._event(actor="someone-else"))
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_control_issue_is_rejected(self) -> None:
        result, _ = self._run_validator(self._event(issue_number=264))
        self.assertNotEqual(result.returncode, 0)

    def test_pull_request_comment_is_rejected(self) -> None:
        result, _ = self._run_validator(self._event(pull_request=True))
        self.assertNotEqual(result.returncode, 0)

    def test_malformed_or_extended_command_is_rejected(self) -> None:
        for body in (
            "/history-evidence",
            "/history-evidence wd-207-c extra",
            "/history-evidence wd-207-c; echo pwned",
            "/history-evidence ../wd-207-c",
        ):
            with self.subTest(body=body):
                result, _ = self._run_validator(self._event(body=body))
                self.assertNotEqual(result.returncode, 0)

    def test_missing_browser_snapshot_is_rejected(self) -> None:
        result, _ = self._run_validator(self._event(), snapshot=False)
        self.assertNotEqual(result.returncode, 0)

    def test_trigger_workflow_uses_event_file_not_comment_shell_interpolation(self) -> None:
        self.assertTrue(TRIGGER_WORKFLOW.exists())
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("issue_comment:", workflow)
        self.assertIn("types: [created]", workflow)
        self.assertIn("$GITHUB_EVENT_PATH", workflow)
        self.assertIn("scripts/validate_history_evidence_dispatch.py", workflow)
        self.assertNotIn("${{ github.event.comment.body }}", workflow)

    def test_trigger_calls_history_sprint_with_fixed_safe_defaults(self) -> None:
        self.assertTrue(TRIGGER_WORKFLOW.exists())
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("uses: ./.github/workflows/history-sprint.yml", workflow)
        self.assertIn('max_issues: "1"', workflow)
        self.assertIn('max_translations: "120"', workflow)
        self.assertIn("categories: browser", workflow)
        self.assertIn('source_run_id: ""', workflow)
        self.assertIn('state_source_run_id: "32734420419"', workflow)
        self.assertIn("strict_final: false", workflow)
        self.assertIn(
            "evidence_issue_ids: ${{ needs.validate.outputs.issue_id }}", workflow
        )
        self.assertIn("capture_wiley_evidence: false", workflow)

    def test_history_sprint_remains_manual_and_becomes_reusable(self) -> None:
        workflow = HISTORY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("workflow_call:", workflow)
        self.assertIn("group: journal-data-update", workflow)



    def test_control_issue_can_trigger_bounded_date_repair_and_wave_a(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.issue.number == 205", workflow)
        self.assertIn("github.event.comment.body == '/history-date-repair'", workflow)
        self.assertIn("github.event.comment.body == '/history-wave-a'", workflow)
        self.assertIn("categories: __date_repair_only__", workflow)
        self.assertIn("repair_dates_only: true", workflow)
        self.assertIn("categories: source_pending,translation_required", workflow)
        self.assertIn("repair_dates_only: false", workflow)


    def test_control_issue_can_trigger_translation_only_wave(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.comment.body == '/history-translation-only'", workflow)
        self.assertIn("categories: translation_required", workflow)
        self.assertIn('max_issues: "10"', workflow)
        self.assertIn('max_translations: "120"', workflow)
        self.assertIn('state_source_run_id: "32734420419"', workflow)
        self.assertIn("repair_dates_only: false", workflow)
        self.assertIn("repair_content_only: false", workflow)


    def test_control_issue_can_trigger_recoverable_wave_b(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.comment.body == '/history-wave-b'", workflow)
        self.assertIn("categories: recoverable", workflow)
        self.assertIn('max_issues: "10"', workflow)
        self.assertIn('state_source_run_id: "32734420419"', workflow)
        self.assertIn("repair_dates_only: false", workflow)




    def test_control_issue_can_trigger_ready_contract_repair(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.comment.body == '/history-ready-contract-repair'", workflow)
        self.assertIn("categories: __ready_contract_repair_only__", workflow)
        self.assertIn("repair_ready_contract_only: true", workflow)
        history = HISTORY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("repair_ready_contract_only", history)
        self.assertIn("scripts/repair_ready_payload_contract.py", history)

    def test_control_issue_can_trigger_content_repair(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.comment.body == '/history-content-repair'", workflow)
        self.assertIn("categories: __content_repair_only__", workflow)
        self.assertIn("repair_content_only: true", workflow)


    def test_control_issue_can_trigger_chicago_evidence_tranche(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.comment.body == '/history-chicago-evidence'", workflow)
        self.assertIn("categories: __chicago_evidence_only__", workflow)
        self.assertIn("jaere-10-1,jaere-10-2", workflow)
        self.assertIn("jaere-11-S1,", workflow)
        self.assertIn("jle-42-S1,jle-43-S1", workflow)
        self.assertIn("repair_dates_only: false", workflow)
        self.assertIn("repair_content_only: false", workflow)


    def test_cer_is_not_routed_through_cambridge_dispatch(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("github.event.comment.body == '/history-cambridge-evidence'", workflow)
        self.assertNotIn('evidence_issue_ids: "cer-80-c,cer-94-c"', workflow)

    def test_control_issue_can_trigger_authenticated_wiley_evidence_tranche(self) -> None:
        workflow = TRIGGER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.comment.body == '/history-wiley-evidence'", workflow)
        self.assertIn("categories: __wiley_evidence_only__", workflow)
        self.assertIn("ajae-107-1,ajae-107-2", workflow)
        self.assertIn("jf-80-5,jf-80-6,", workflow)
        self.assertIn("ier-64-1,ier-64-2,ier-64-3", workflow)
        wiley_block = workflow.split("  wiley_evidence:", 1)[1].split("\n  ", 1)[0]
        self.assertIn("capture_wiley_evidence: true", wiley_block)
        self.assertIn("repair_dates_only: false", workflow)
        self.assertIn("repair_content_only: false", workflow)


if __name__ == "__main__":
    unittest.main()
