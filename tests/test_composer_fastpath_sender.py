from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ComposerFastPathSenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/update-journals.yml").read_text(encoding="utf-8")
        cls.sender = (ROOT / "scripts/request_composer_reconcile.sh").read_text(encoding="utf-8")
        cls.smoke = (ROOT / ".github/workflows/composer-dispatch-acceptance.yml").read_text(encoding="utf-8")
        cls.monitor = (ROOT / ".github/workflows/monitor-journals.yml").read_text(encoding="utf-8")

    def test_request_follows_successful_private_sync_and_precedes_email(self):
        lines = self.workflow
        assert lines.index("Sync audited journal issues to private Composer") < lines.index("Request Composer runner reconciliation") < lines.index("Send private new-issue email")
        self.assertIn("if: steps.collect.outcome == 'success' && steps.composer.outcome == 'success'", lines)
        self.assertIn("continue-on-error: true", lines.split("- name: Request Composer runner reconciliation",1)[1].split("- name: Send private new-issue email",1)[0])
        self.assertIn("if: steps.collect.outcome == 'success' && steps.composer.outcome == 'success'",lines.split("- name: Send private new-issue email",1)[1])

    def test_monitor_dispatch_is_guarded_by_real_private_sync(self):
        workflow = self.monitor
        self.assertLess(workflow.index("Sync audited issues to private Composer"), workflow.index("Request Composer runner reconciliation"))
        self.assertLess(workflow.index("Request Composer runner reconciliation"), workflow.index("Send private new-issue email"))
        section = workflow.split("- name: Request Composer runner reconciliation", 1)[1].split("- name: Send private new-issue email", 1)[0]
        self.assertIn("steps.changed.outputs.has_updates == 'true' && steps.composer.outcome == 'success'", section)
        self.assertIn("continue-on-error: true", section)
        self.assertIn("COMPOSER_RUNNER_DISPATCH_TOKEN", section)
        self.assertIn("run: bash scripts/request_composer_reconcile.sh", section)

    def test_request_only_and_target_scoped(self):
        self.assertIn("COMPOSER_RUNNER_DISPATCH_TOKEN", self.workflow)
        self.assertIn("COMPOSER_RUNNER_DISPATCH_TOKEN", self.smoke)
        self.assertIn("repos/academic-door/academic-door-composer-runner/dispatches", self.sender)
        self.assertIn("event_type=composer-reconcile", self.sender)
        self.assertNotIn("client_payload", self.sender.replace("# No client_payload, private SHA/ref, or Cloudflare credentials cross the boundary.", ""))
        self.assertNotIn("CLOUDFLARE_API_TOKEN",self.sender)
        self.assertNotIn("COMPOSER_DEPLOY_KEY",self.sender)
        self.assertIn("gh api --method POST",self.sender)
        self.assertIn("run: bash scripts/request_composer_reconcile.sh",self.smoke)
        self.assertIn("run: bash scripts/request_composer_reconcile.sh",self.workflow)

    def test_smoke_is_credential_check_on_sender_change_only(self):
        self.assertIn("branches: [main]",self.smoke)
        self.assertIn(".github/workflows/update-journals.yml",self.smoke)
        self.assertIn("permissions:\n  contents: read", self.smoke)
        self.assertNotIn("schedule:",self.smoke)

if __name__ == "__main__":
    unittest.main()
