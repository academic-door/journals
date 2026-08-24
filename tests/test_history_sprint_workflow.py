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
        self.assertIn("--state-root data/backfill-state", workflow)
        history = workflow.index("python scripts/audit_history_coverage.py")
        privacy = workflow.index("python scripts/audit_privacy.py")
        self.assertIn('if [ "${{ inputs.strict_final }}" = "true" ]', workflow)
        self.assertIn("history_args+=(--strict)", workflow)
        self.assertIn("--translate", workflow[evidence:audit])
        self.assertIn("DEEPSEEK_API_KEY", workflow)

    def test_can_retry_only_named_official_evidence_without_reprocessing_all(self) -> None:
        workflow = self.workflow()
        self.assertIn("evidence_issue_ids:", workflow)
        self.assertIn("EVIDENCE_ISSUE_IDS: ${{ inputs.evidence_issue_ids }}", workflow)
        self.assertIn("Convert ScienceDirect browser snapshots to official evidence", workflow)
        self.assertIn("capture_sciencedirect_browser_roster_evidence.py", workflow)
        self.assertIn("--output-root data/provenance/official-rosters/sciencedirect", workflow)
        self.assertIn("Browser-authorized snapshot not found for", workflow)
        self.assertIn('[[ ! "$issue_id" =~ ^[a-z0-9-]+$ ]]', workflow)
        self.assertIn('-name "$issue_id.json"', workflow)
        self.assertIn("Official evidence not found for", workflow)
        self.assertIn("ELSEVIER_API_KEY", workflow)
        self.assertIn("--enrich-missing-elsevier", workflow)


if __name__ == "__main__":
    unittest.main()
