from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_recovery_progress import (
    evidence_revalidation_requested,
    evaluate_progress,
)


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

    def test_only_complete_named_evidence_marker_enables_revalidation_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "sciencedirect-evidence-deferred.txt"
            self.assertFalse(evidence_revalidation_requested(marker))
            marker.write_text("wd-missing-c\n", encoding="utf-8")
            self.assertFalse(evidence_revalidation_requested(marker))
            marker.write_text("", encoding="utf-8")
            self.assertTrue(evidence_revalidation_requested(marker))

    def test_history_sprint_marker_is_scoped_to_named_evidence_step(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "history-sprint.yml").read_text(
            encoding="utf-8"
        )
        build = workflow.index("Build historical archives from ScienceDirect browser snapshots")
        convert = workflow.index("Convert ScienceDirect browser snapshots to official evidence")
        block = workflow[build:convert]
        self.assertIn("if: inputs.evidence_issue_ids != ''", block)
        self.assertIn(
            'cp "$deferred" "$GITHUB_WORKSPACE/sciencedirect-evidence-deferred.txt"',
            block,
        )


if __name__ == "__main__":
    unittest.main()
