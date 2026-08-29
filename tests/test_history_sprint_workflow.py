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

    def test_repairs_only_invalid_current_translations_before_acceptance(self) -> None:
        workflow = self.workflow()
        repair = workflow.index("python scripts/repair_current_translations.py")
        audit = workflow.index("python scripts/audit_public_data.py --strict-provenance")
        self.assertLess(repair, audit)
        self.assertIn("--translation-cache-root data/translation-cache", workflow[repair:audit])
        self.assertIn("current-translation-repair.json", workflow[repair:audit])

    def test_can_limit_a_wave_to_named_issues_and_skip_source_steps_for_translation(self) -> None:
        workflow = self.workflow()
        self.assertIn("issue_ids:", workflow)
        self.assertIn('--issue-ids "${{ inputs.issue_ids }}"', workflow)
        self.assertIn("if: inputs.evidence_issue_ids != ''", workflow)

    def test_can_publish_only_a_verified_issue_subset(self) -> None:
        workflow = self.workflow()
        self.assertIn("publish_issue_ids:", workflow)
        self.assertIn('--issue-ids "${{ inputs.publish_issue_ids }}"', workflow)
        self.assertIn("if: inputs.evidence_issue_ids != ''", workflow)

    def test_one_failed_shard_still_uploads_partial_evidence(self) -> None:
        workflow = self.workflow()
        run = workflow.index("Run both historical year windows in this shard")
        upload = workflow.index("Upload isolated shard staging")
        self.assertIn("continue-on-error: true", workflow[run:upload])
        self.assertIn("if: always()", workflow[upload:])
        self.assertIn("if-no-files-found: warn", workflow[upload:])

    def test_missing_browser_snapshot_directory_is_non_fatal(self) -> None:
        workflow = self.workflow()
        self.assertGreaterEqual(
            workflow.count("mkdir -p data/provenance/browser-snapshots"),
            2,
        )

    def test_optional_publisher_steps_do_not_abort_the_publish_job(self) -> None:
        workflow = self.workflow()
        for step in (
            "Build historical archives from ScienceDirect browser snapshots",
            "Convert ScienceDirect browser snapshots to official evidence",
            "Capture official Wiley roster evidence",
        ):
            start = workflow.index(step)
            next_step = workflow.find("      - name:", start + 1)
            section = workflow[start:next_step if next_step != -1 else None]
            self.assertIn("continue-on-error: true", section)

    def test_restores_browser_snapshots_from_the_data_branch(self) -> None:
        workflow = self.workflow()
        self.assertIn("data/provenance/browser-snapshots", workflow)
        self.assertIn(
            "git archive origin/data data/provenance/browser-snapshots | tar -x",
            workflow,
        )

    def test_builds_archives_from_generic_roster_evidence_before_import(self) -> None:
        workflow = self.workflow()
        build = workflow.index(
            "python scripts/build_archives_from_roster_evidence.py"
        )
        apply = workflow.index("Apply exact official roster evidence")
        self.assertLess(build, apply)
        self.assertIn("SEMANTIC_SCHOLAR_API_KEY", workflow)
        self.assertIn("capture_springer_roster_evidence.py", workflow)
        self.assertIn("data/provenance/official-rosters/springer", workflow)
        self.assertIn("capture_cambridge_roster_evidence.py", workflow)
        self.assertIn("data/provenance/official-rosters/cambridge", workflow)
        self.assertIn("EVIDENCE_ISSUE_IDS: ${{ inputs.evidence_issue_ids }}", workflow)
        self.assertIn('evidence_args+=(--issue-ids "$EVIDENCE_ISSUE_IDS")', workflow)

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
