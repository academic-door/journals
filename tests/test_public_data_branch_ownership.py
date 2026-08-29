from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    name: ROOT / ".github" / "workflows" / name
    for name in (
        "deploy.yml",
        "update-journals.yml",
        "monitor-journals.yml",
        "backfill-field-history.yml",
        "test-email-notification.yml",
    )
}
PUBLISHERS = (
    "update-journals.yml",
    "monitor-journals.yml",
    "backfill-field-history.yml",
)
ACTIVE_DATA_PUBLISHERS = (
    "update-journals.yml",
    "monitor-journals.yml",
    "backfill-field-history.yml",
)


class PublicDataBranchOwnershipTests(unittest.TestCase):
    def workflow(self, name: str) -> str:
        return WORKFLOWS[name].read_text(encoding="utf-8")

    def test_data_overlay_is_allowlisted_in_every_consumer(self) -> None:
        expected = (
            "git archive origin/data public/api public/project-manifest.json | tar -x"
        )
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                text = self.workflow(name)
                self.assertIn(expected, text)
                self.assertIn("origin/data:public/backfill-status.md", text)
                self.assertNotIn("git archive origin/data public | tar -x", text)

    def test_workflows_never_replace_or_stage_the_whole_public_tree(self) -> None:
        forbidden = {
            "delete": re.compile(r"^\s*rm -rf public(?:\s|$)", re.MULTILINE),
            "stage": re.compile(r"^\s*git add public(?:\s|$)", re.MULTILINE),
            "copy": re.compile(r"cp -R (?:public/\.|\"\$generated/public/\.\") public/"),
        }
        for name in WORKFLOWS:
            text = self.workflow(name)
            for operation, pattern in forbidden.items():
                with self.subTest(workflow=name, operation=operation):
                    self.assertIsNone(pattern.search(text))

    def test_publishers_prune_the_legacy_search_script_from_data(self) -> None:
        for name in PUBLISHERS:
            with self.subTest(workflow=name):
                text = self.workflow(name)
                self.assertRegex(
                    text,
                    r'git(?: -C "\$data_tree")? rm -f --ignore-unmatch public/search\.js',
                )
                self.assertRegex(
                    text,
                    r'git(?: -C "\$data_tree")? add -A public/api',
                )

    def test_all_active_data_writers_share_one_concurrency_group(self) -> None:
        for name in ACTIVE_DATA_PUBLISHERS:
            with self.subTest(workflow=name):
                text = self.workflow(name)
                self.assertIn("group: journal-data-update", text)
        self.assertNotIn(
            "field-history-backfill-",
            self.workflow("backfill-field-history.yml"),
        )

    def test_publish_retries_refetch_and_apply_a_file_delta(self) -> None:
        for name in ACTIVE_DATA_PUBLISHERS:
            with self.subTest(workflow=name):
                text = self.workflow(name)
                self.assertIn("publish_data_delta.py apply", text)
                self.assertIn("for attempt in 1 2 3 4 5", text)
                self.assertIn("git fetch origin data", text)

    def test_publication_baseline_is_captured_after_data_restore(self) -> None:
        restore_labels = {
            "update-journals.yml": "Restore last-known-good data baseline",
            "monitor-journals.yml": "Restore last-known-good public data and monitor state",
            "backfill-field-history.yml": "Restore public data and resumable backfill state",
        }
        for name, restore_label in restore_labels.items():
            with self.subTest(workflow=name):
                text = self.workflow(name)
                self.assertLess(
                    text.index(restore_label),
                    text.index("Snapshot data publication baseline"),
                )

    def test_active_publish_steps_do_not_replace_public_api_wholesale(self) -> None:
        sections = {
            "update-journals.yml": self.workflow("update-journals.yml").split(
                "- name: Publish generated public data to data branch", 1
            )[1].split("- name: Preserve partial enrichment", 1)[0],
            "monitor-journals.yml": self.workflow("monitor-journals.yml").split(
                "- name: Publish data and monitoring state", 1
            )[1].split("- name: Sync audited issues", 1)[0],
            "backfill-field-history.yml": self.workflow(
                "backfill-field-history.yml"
            ).split("- name: Publish validated archives", 1)[1].split(
                "- name: Preserve failed-run", 1
            )[0],
        }
        for name, section in sections.items():
            with self.subTest(workflow=name):
                self.assertNotIn("rm -rf public/api", section)
                self.assertIn("--baseline", section)
                self.assertIn("--generated", section)
                self.assertIn("--target", section)
                self.assertIn("--path public/api/v1/journals", section)
                self.assertIn(
                    "--exclude 'public/api/v1/journals/*/issues/index.json'",
                    section,
                )

    def test_derived_public_indexes_are_rebuilt_from_the_fresh_tree(self) -> None:
        sections = {
            name: self.workflow(name).split(
                {
                    "update-journals.yml": "- name: Publish generated public data",
                    "monitor-journals.yml": "- name: Publish data and monitoring state",
                    "backfill-field-history.yml": "- name: Publish validated archives",
                }[name],
                1,
            )[1]
            for name in ACTIVE_DATA_PUBLISHERS
        }
        for name, section in sections.items():
            with self.subTest(workflow=name):
                self.assertNotRegex(section, r"--path public/api\s*(?:\\|$)")
                rebuild = section.index("python scripts/rebuild_public_snapshot.py\n")
                check = section.index("python scripts/rebuild_public_snapshot.py --check")
                sync = section.index('cp -R "$GITHUB_WORKSPACE/public/api/."')
                self.assertLess(rebuild, check)
                self.assertLess(check, sync)
                self.assertIn("rebuild-archive-indexes", section)
                self.assertIn("scripts/backfill_status.py", section)
                self.assertIn("scripts/audit_history_coverage.py", section)

    def test_failed_batches_never_stage_public_api(self) -> None:
        update_failure = self.workflow("update-journals.yml").split(
            "- name: Preserve partial enrichment", 1
        )[1].split("- name: Sync audited journal issues", 1)[0]
        backfill_failure = self.workflow("backfill-field-history.yml").split(
            "- name: Preserve failed-run cache and checkpoints only", 1
        )[1]
        for name, section in (
            ("update-journals.yml", update_failure),
            ("backfill-field-history.yml", backfill_failure),
        ):
            with self.subTest(workflow=name):
                self.assertNotIn("--path public/api", section)
                self.assertNotRegex(section, r"git .*add .*public/api")

    def test_deploy_audits_the_overlaid_data_before_build(self) -> None:
        text = self.workflow("deploy.yml")
        overlay = text.index("Overlay generated data branch")
        audit = text.index("Audit overlaid release data")
        build = text.index("pnpm run build")
        self.assertLess(overlay, audit)
        self.assertLess(audit, build)
        self.assertIn("audit_public_data.py --strict-provenance", text)
        self.assertIn("python scripts/rebuild_public_snapshot.py\n", text)
        self.assertIn("audit_source_alignment.py", text)
        self.assertIn("audit_privacy.py", text)
        self.assertIn("audit_history_coverage.py", text)
        self.assertIn("STRICT_HISTORY_COVERAGE_ENABLED", text)
        self.assertIn("tests.test_history_period_audit", text)

    def test_health_check_validates_json_semantics_and_freshness(self) -> None:
        text = (ROOT / ".github" / "workflows" / "health.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("enabled_journals", text)
        self.assertIn("translated_articles", text)
        self.assertIn("configured_journals", text)
        self.assertIn("source-audit.json", text)
        self.assertIn("is stale", text)
        self.assertIn("Close recovered health issue", text)

    def test_agent_checkin_pushes_only_to_activity_branch(self) -> None:
        text = (
            ROOT / ".github" / "workflows" / "scheduled-agent-checkin.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("git switch -C activity origin/activity", text)
        self.assertIn("git push origin HEAD:activity", text)
        self.assertNotRegex(text, r"(?m)^\s*git push\s*$")

    def test_deprecated_top5_backfill_is_a_read_only_notice(self) -> None:
        text = (
            ROOT / ".github" / "workflows" / "backfill-history.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("rm -rf public/api", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("origin/data", text)

    def test_search_script_remains_a_main_owned_asset(self) -> None:
        search = (ROOT / "public" / "search.js").read_text(encoding="utf-8")
        self.assertIn("const initializeFromQuery = async () =>", search)
        self.assertIn("params.get(\"china\") === \"1\"", search)


if __name__ == "__main__":
    unittest.main()
