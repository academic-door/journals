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

    def test_no_deploy_health_loop(self) -> None:
        # health.yml must not itself be one of the workflows that trigger deploy,
        # and must not trigger the deploy workflow.
        workflow_run = _on(self.payload).get("workflow_run", {})
        triggering = workflow_run.get("workflows", [])
        self.assertNotIn("Public health check", triggering)
        self.assertNotIn("Deploy GitHub Pages", [self.payload.get("name")])


if __name__ == "__main__":
    unittest.main()
