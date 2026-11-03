"""Regression lock for the post-deploy public-health control contract.

After a successful Pages deployment we require independent public health.
This must not create a deploy->health loop, and a failed deploy must never be
able to produce a healthy acceptance.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / ".github" / "workflows" / "health.yml"


def _on(payload: dict) -> dict:
    """GitHub ``on:`` is read back as ``True`` by YAML 1.1 loaders."""
    return payload.get("on") or payload.get(True) or {}


class HealthWorkflowControlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = yaml.safe_load(HEALTH.read_text(encoding="utf-8"))

    def test_health_triggered_after_pages_deploy(self) -> None:
        workflow_run = _on(self.payload).get("workflow_run")
        self.assertIsInstance(workflow_run, dict)
        self.assertIn("Deploy GitHub Pages", workflow_run.get("workflows", []))
        self.assertIn("completed", workflow_run.get("types", []))

    def test_success_only_guard_prevents_failed_deploy_to_pass_health(self) -> None:
        job = self.payload["jobs"]["check"]
        guard = str(job.get("if", ""))
        # For workflow_run events the check must only run on a successful deploy.
        self.assertIn("github.event.workflow_run.conclusion == 'success'", guard)
        # And it must still allow manual/scheduled runs (non-workflow_run).
        self.assertIn("github.event_name != 'workflow_run'", guard)

    def test_first_b8_title_extends_count_without_relaxing_health_gates(self) -> None:
        workflow = HEALTH.read_text(encoding="utf-8")
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn('python scripts/verify_public_health.py "$output"', workflow)
        self.assertNotIn("python - \"$output\" <<'PY'", workflow)
        # The extracted validator is a byte-for-byte deindent of the old
        # embedded Python body. Its substantive assertions are unchanged.
        raw = (ROOT / "scripts/verify_public_health.py").read_text(encoding="utf-8")
        self.assertIn("expected not in (49, 50, 51, 52, 53, 54)", raw)
        self.assertIn('if monitoring.get("status") != "healthy":', raw)
        self.assertIn('if source.get("status") != "healthy":', raw)
        self.assertIn('if summary.get("available_journals") != expected:', raw)
        self.assertIn('if monitor_summary.get("configured_journals") != expected:', raw)
        self.assertIn('if source_summary.get("configured_journals") != expected:', raw)
        self.assertIn('if reconciliation.get("journal_count") != expected:', raw)
        self.assertIn('if expected >= 50:', raw)
        self.assertIn('"10.1007/s10887-026-09265-x"', raw)
        self.assertIn('"10.1007/s10887-026-09267-9"', raw)
        self.assertIn('"10.1007/s10887-026-09266-w"', raw)
        self.assertIn('growth.get("publication_state") != "ready"', raw)
        self.assertIn('if expected >= 50 and "jegrowth" not in reported_transport:', raw)
        self.assertIn("if expected >= 51:", raw)
        self.assertIn('jae.get("issue_id") != "jae-41-6"', raw)
        self.assertIn('"10.1002/jae.70067"', raw)
        self.assertIn('"10.1002/jae.70072"', raw)
        self.assertIn('jae.get("public_mode") != "metadata_first_rights_qualified_v1"', raw)
        self.assertIn('rights.get("rights_withheld_articles") != 12', raw)
        self.assertIn('summary.get("translated_articles") + expected_withheld != summary.get("articles")', raw)
        self.assertIn('and monitoring.get("warning_journals") == [', raw)
        self.assertIn('if not pending_jae:', raw)
        self.assertIn('if expected >= 53:', raw)
        self.assertIn('if expected == 54:', raw)
        self.assertIn('ijio.get("issue_id") != "ijio-108-c"', raw)
        self.assertIn('"10.1016/j.ijindorg.2026.103282"', raw)
        self.assertIn('"10.1016/j.ijindorg.2026.103311"', raw)
        self.assertIn('rights.get("rights_withheld_articles") != 6', raw)
        self.assertIn('joeg.get("issue_id") != "joeg-26-5"', raw)
        self.assertIn('joeg_q.get("roster_authority") != "official-issue-page"', raw)
        self.assertIn('"10.1093/jeg/lbag036"', raw)
        self.assertIn('"10.1093/jeg/lbag009"', raw)
        self.assertIn('"10.1093/jeg/lbag061"', raw)
        self.assertIn('["comment"] * 3 + ["research-article"] * 6', raw)
        self.assertIn('joeg_rights.get("rights_withheld_articles") != 9', raw)
        self.assertIn('expected_withheld = {51: 12, 52: 23, 53: 32, 54: 38}[expected]', raw)
        self.assertIn('if expected >= 52:', raw)
        self.assertIn('itpf.get("issue_id") != "itpf-33-5"', raw)
        self.assertIn('itpf.get("source_status") != "publisher_verified"', raw)
        self.assertIn('q.get("roster_authority") != "repec-publisher-supplied"', raw)
        self.assertIn('"10.1007/s10797-026-09955-7"', raw)
        self.assertIn('"10.1007/s10797-025-09928-2"', raw)
        self.assertIn('rights.get("rights_withheld_articles") != 11', raw)
        self.assertIn('expected_withheld = {51: 12, 52: 23, 53: 32, 54: 38}[expected]', raw)

    def test_no_deploy_health_loop(self) -> None:
        # health.yml must not itself be one of the workflows that trigger deploy,
        # and must not trigger the deploy workflow.
        workflow_run = _on(self.payload).get("workflow_run", {})
        triggering = workflow_run.get("workflows", [])
        self.assertNotIn("Public health check", triggering)
        self.assertNotIn("Deploy GitHub Pages", [self.payload.get("name")])


if __name__ == "__main__":
    unittest.main()
