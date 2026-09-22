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
        self.assertIn('--explicit "${{ inputs.evidence_issue_ids }}"', workflow)
        self.assertIn('--github-env "$GITHUB_ENV"', workflow)
        self.assertIn('IFS=\',\' read -ra issue_ids <<< "${RECOVERY_EVIDENCE_ISSUE_IDS:-}"', workflow)
        self.assertIn("Convert ScienceDirect browser snapshots to official evidence", workflow)
        self.assertIn("capture_sciencedirect_browser_roster_evidence.py", workflow)
        self.assertIn("--output-root data/provenance/official-rosters/sciencedirect", workflow)
        self.assertIn("Browser-authorized snapshot not found for", workflow)
        self.assertIn('[[ ! "$issue_id" =~ ^[A-Za-z0-9-]+$ ]]', workflow)
        self.assertIn('-name "$issue_id.json"', workflow)
        self.assertIn("Official evidence not found for", workflow)
        self.assertIn("ELSEVIER_API_KEY", workflow)

    def test_evidence_only_dispatch_publishes_without_recovery_shards(self) -> None:
        workflow = self.workflow()
        publish = workflow.index("  publish:")
        composer = workflow.index("  composer:", publish)
        publish_block = workflow[publish:composer]
        evidence_only = "inputs.evidence_issue_ids != ''"
        shard_work = "inputs.source_run_id != '' || needs.collect.result != 'skipped'"
        self.assertIn(
            f"if: always() && ({evidence_only} || {shard_work} || inputs.repair_dates_only || inputs.repair_content_only || inputs.repair_ready_contract_only)",
            publish_block,
        )
        for step in (
            "Download all isolated shards",
            "Exclude prior final reports from shard merge",
            "Merge shard changes into the latest data baseline",
        ):
            start = publish_block.index(f"- name: {step}")
            window = publish_block[start : start + 260]
            self.assertIn(f"if: {shard_work}", window)

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
        self.assertIn('if [[ -z "${RECOVERY_EVIDENCE_ISSUE_IDS:-}" ]]', workflow)
        self.assertIn('--issue-ids "$RECOVERY_EVIDENCE_ISSUE_IDS"', workflow)

    def test_cambridge_capture_runs_before_generic_roster_build(self) -> None:
        workflow = self.workflow()
        capture = workflow.index("Capture official Cambridge roster evidence")
        generic = workflow.index("Build archives from generic official roster evidence")
        self.assertLess(capture, generic)
        self.assertIn("if: inputs.capture_cambridge_evidence", workflow)
        self.assertIn("scripts/capture_cambridge_roster_evidence.py", workflow)
        self.assertIn("--diagnostics-root", workflow)

    def test_publish_evidence_is_bounded_to_current_recovery_queue(self) -> None:
        workflow = self.workflow()
        self.assertIn("Scope official evidence to this recovery run", workflow)
        self.assertIn("python scripts/recovery_evidence_scope.py", workflow)
        self.assertIn('--github-env "$GITHUB_ENV"', workflow)
        self.assertIn('--issue-ids "$RECOVERY_EVIDENCE_ISSUE_IDS"', workflow)
        self.assertNotIn(
            "find data/provenance/official-rosters -type f -name '*.json' -print0",
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




    def test_sciencedirect_roster_converter_receives_elsevier_metadata_credentials(self) -> None:
        workflow = self.workflow()
        start = workflow.index("Convert ScienceDirect browser snapshots to official evidence")
        end = workflow.index("Capture official Wiley roster evidence", start)
        window = workflow[start:end]
        self.assertIn("ELSEVIER_API_KEY: ${{ secrets.ELSEVIER_API_KEY }}", window)
        self.assertIn("ELSEVIER_INST_TOKEN: ${{ secrets.ELSEVIER_INST_TOKEN }}", window)
        self.assertIn("capture_sciencedirect_browser_roster_evidence.py", window)

    def test_trusted_elsevier_staging_lane_skips_browser_reconstruction(self) -> None:
        workflow = self.workflow()
        guard = "if: inputs.evidence_issue_ids != '' && inputs.categories != '__elsevier_staging_only__'"
        for step in (
            "Build historical archives from ScienceDirect browser snapshots",
            "Convert ScienceDirect browser snapshots to official evidence",
        ):
            start = workflow.index(f"- name: {step}")
            window = workflow[start : start + 260]
            self.assertIn(guard, window)

        helper = workflow.index("Recover trusted Elsevier staging metadata")
        helper_window = workflow[helper : helper + 220]
        self.assertIn("if: inputs.evidence_issue_ids != ''", helper_window)
        self.assertNotIn("__elsevier_staging_only__", helper_window)


    def test_date_repair_skips_only_recovery_count_progress_gate(self) -> None:
        workflow = self.workflow()
        progress = workflow.index("python scripts/check_recovery_progress.py")
        strict_audit = workflow.index(
            "python scripts/audit_public_data.py --strict-provenance --strict-history-period-duplicates"
        )
        window = workflow[progress - 220 : strict_audit]
        self.assertIn('inputs.repair_dates_only', window)
        self.assertIn('!= "true"', window)
        self.assertIn(
            "Repair-only run: recovery-count progress gate is not applicable.",
            window,
        )
        self.assertLess(progress, strict_audit)


    def test_date_repair_enables_duplicate_period_gate(self) -> None:
        workflow = self.workflow()
        self.assertIn(
            "python scripts/audit_public_data.py --strict-provenance --strict-history-period-duplicates",
            workflow,
        )

    def test_repairs_duplicate_month_elsevier_history_before_audit(self) -> None:
        workflow = self.workflow()
        repair = workflow.index("python scripts/repair_history_dates.py")
        audit = workflow.index("python scripts/audit_public_data.py --strict-provenance")
        for journal in (
            "EER", "FOODPOLICY", "GEB", "JCE", "JDE", "JEBO", "JEEM",
            "JHE", "JIE", "JME", "LABECO", "LUP", "WD",
        ):
            self.assertIn(f"--journal {journal}", workflow[repair:audit])

    def test_supports_date_repair_only_without_recovery_or_composer_sync(self) -> None:
        workflow = self.workflow()
        self.assertIn("repair_dates_only:", workflow)
        self.assertIn("|| inputs.repair_dates_only || inputs.repair_content_only || inputs.repair_ready_contract_only)", workflow)
        self.assertIn("inputs.repair_dates_only == false", workflow)
        self.assertIn(
            "needs.publish.result == 'success' && inputs.repair_dates_only == false",
            workflow,
        )



    def test_content_repair_is_bounded_and_requires_composer_success(self) -> None:
        workflow = self.workflow()
        self.assertIn("repair_content_only:", workflow)
        self.assertIn("python scripts/repair_no_abstract_comments.py", workflow)
        self.assertIn("python scripts/repair_no_abstract_comments.py --check", workflow)
        self.assertIn("inputs.repair_content_only == false", workflow)
        self.assertIn("inputs.repair_content_only || inputs.repair_ready_contract_only", workflow)
        self.assertIn(
            "continue-on-error: ${{ inputs.repair_content_only == false && inputs.repair_ready_contract_only == false }}",
            workflow,
        )

    def test_ready_contract_repair_is_bounded_and_requires_composer_success(self) -> None:
        workflow = self.workflow()
        self.assertIn("repair_ready_contract_only:", workflow)
        self.assertIn("python scripts/repair_ready_payload_contract.py --api-root public/api/v1", workflow)
        self.assertIn("inputs.repair_ready_contract_only == false", workflow)
        self.assertIn("|| inputs.repair_ready_contract_only)", workflow)
        self.assertIn(
            "continue-on-error: ${{ inputs.repair_content_only == false && inputs.repair_ready_contract_only == false }}",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
