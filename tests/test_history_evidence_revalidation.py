from __future__ import annotations

import unittest
from pathlib import Path

from scripts.check_recovery_progress import evaluate_progress


def record(issue_id: str, category: str) -> dict:
    return {"issue_id": issue_id, "category": category, "counts": {}, "archive_exists": True}


class HistoryEvidenceRevalidationTests(unittest.TestCase):
    def test_explicit_evidence_revalidation_can_accept_no_delta(self) -> None:
        state = {"records": [record("wd-207-c", "ready")]}
        result = evaluate_progress(state, state, allow_no_progress=True)
        self.assertTrue(result["ok"])
        self.assertNotIn("wave produced no measurable progress", result["errors"])

    def test_regular_recovery_still_rejects_no_delta(self) -> None:
        state = {"records": [record("wd-207-c", "ready")]}
        result = evaluate_progress(state, state)
        self.assertFalse(result["ok"])
        self.assertIn("wave produced no measurable progress", result["errors"])

    def test_history_sprint_scopes_no_progress_override_to_named_evidence(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "history-sprint.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("progress_args=()", workflow)
        self.assertIn("progress_args+=(--allow-no-progress)", workflow)
        self.assertIn('"${progress_args[@]}"', workflow)
        self.assertIn('if [[ -n "$EVIDENCE_ISSUE_IDS" ]]; then', workflow)


if __name__ == "__main__":
    unittest.main()
