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
        for journal in ("JPubE", "ENERGY", "ECOLECON", "JUE", "JET", "JEDC", "JOE", "JFE"):
            self.assertIn(f"--journal {journal}", workflow[repair:audit])
        self.assertLess(repair, audit)

    def test_can_reuse_successful_shards_without_recollecting(self) -> None:
        workflow = self.workflow()
        self.assertIn("source_run_id:", workflow)
        self.assertIn("inputs.source_run_id == '' && needs.prepare.outputs.has_work == 'true'", workflow)
        self.assertIn("inputs.source_run_id != '' || needs.collect.result != 'skipped'", workflow)
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
        self.assertIn('[[ ! "$issue_id" =~ ^[A-Za-z0-9-]+$ ]]', workflow)
        self.assertIn('-name "$issue_id.json"', workflow)
        self.assertIn("Official evidence not found for", workflow)
        self.assertIn("ELSEVIER_API_KEY", workflow)

    def test_uses_exact_short_shards_and_publishes_successful_partial_results(self) -> None:
        workflow = self.workflow()
        self.assertIn("python scripts/build_recovery_queue.py", workflow)
        self.assertIn("matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}", workflow)
        self.assertIn("timeout-minutes: 20", workflow)
        self.assertIn('--issue-ids "$SHARD_ISSUE_IDS"', workflow)
        self.assertIn("--max-minutes 10", workflow)
        self.assertIn("needs: [prepare, collect]", workflow)
        self.assertIn("python scripts/check_recovery_progress.py", workflow)
        self.assertIn("data/provenance/official-rosters", workflow)

    def test_restores_browser_snapshots_from_the_data_branch(self) -> None:
        workflow = self.workflow()
        self.assertIn("data/provenance/browser-snapshots", workflow)
        self.assertIn(
            "git archive origin/data data/provenance/browser-snapshots | tar -x",
            workflow,
        )

    def test_passes_shared_semantic_scholar_key_without_exposing_it(self) -> None:
        workflow = self.workflow()
        self.assertIn(
            "SEMANTIC_SCHOLAR_API_KEY: ${{ secrets.SEMANTIC_SCHOLAR_API_KEY }}",
            workflow,
        )
        self.assertIn("--enrich-missing-elsevier", workflow)

    def test_builds_missing_historical_archives_before_roster_evidence(self) -> None:
        workflow = self.workflow()
        build = workflow.index("python scripts/build_sciencedirect_browser_archives.py")
        evidence = workflow.index(
            "python scripts/capture_sciencedirect_browser_roster_evidence.py"
        )
        self.assertLess(build, evidence)
        self.assertIn("--state-root data/backfill-state", workflow[build:evidence])
        self.assertIn("--translation-cache-root data/translation-cache", workflow[build:evidence])
        self.assertIn("--translate", workflow[build:evidence])
        self.assertIn("ELSEVIER_API_KEY", workflow)


if __name__ == "__main__":
    unittest.main()
