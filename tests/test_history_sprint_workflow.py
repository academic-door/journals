from __future__ import annotations

import unittest
from pathlib import Path


class HistorySprintWorkflowTests(unittest.TestCase):
    def workflow(self) -> str:
        root = Path(__file__).resolve().parents[1]
        return (root / ".github" / "workflows" / "history-sprint.yml").read_text(
            encoding="utf-8"
        )

    def test_repairs_jpube_dates_before_strict_audit(self) -> None:
        workflow = self.workflow()
        repair = workflow.index("python scripts/repair_history_dates.py")
        audit = workflow.index("python scripts/audit_public_data.py --strict-provenance")
        self.assertIn("--journal JPubE", workflow[repair:audit])
        self.assertLess(repair, audit)

    def test_can_reuse_successful_shards_without_recollecting(self) -> None:
        workflow = self.workflow()
        self.assertIn("source_run_id:", workflow)
        self.assertIn("if: inputs.source_run_id == ''", workflow)
        self.assertIn("needs.collect.result == 'success' || inputs.source_run_id != ''", workflow)
        self.assertIn("run-id: ${{ inputs.source_run_id || github.run_id }}", workflow)
        self.assertIn("github-token: ${{ github.token }}", workflow)
        self.assertIn("rm -rf shards/history-sprint-final-reports", workflow)

    def test_applies_official_evidence_before_audit_and_runs_composer_after_reuse(self) -> None:
        workflow = self.workflow()
        evidence = workflow.index("python scripts/import_official_roster_evidence.py")
        audit = workflow.index("python scripts/audit_public_data.py --strict-provenance")
        self.assertLess(evidence, audit)
        self.assertIn("if: always() && needs.publish.result == 'success'", workflow)


if __name__ == "__main__":
    unittest.main()
