from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "mailbox-freshness.yml"
CONFIG = ROOT / "config" / "mailbox-announcements.yml"
READER = ROOT / "scripts" / "mailbox_announcements.py"


class MailboxWorkflowContractTests(unittest.TestCase):
    def workflow(self) -> str:
        self.assertTrue(WORKFLOW.exists(), "mailbox freshness workflow is not implemented")
        return WORKFLOW.read_text(encoding="utf-8")

    def config(self) -> dict:
        self.assertTrue(CONFIG.exists(), "mailbox announcement allowlist is not implemented")
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}

    def test_allowlist_covers_current_awaiting_official_journals(self):
        rules = self.config().get("mailbox_announcements", {})
        self.assertEqual(set(rules), {"JPE", "ECTA", "RAND", "JAERE", "ERE"})
        expected_ids = {
            "JPE": "jpe",
            "ECTA": "ecta",
            "RAND": "rand",
            "JAERE": "jaere",
            "ERE": "ere",
        }
        for key, journal_id in expected_ids.items():
            with self.subTest(journal=key):
                rule = rules[key]
                self.assertEqual(rule.get("journal_id"), journal_id)
                for field in ("sender_domains", "subject_patterns", "official_link_hosts"):
                    self.assertIsInstance(rule.get(field), list)
                    self.assertTrue(rule[field])

    def test_workflow_is_read_only_mailbox_polling_with_single_writer_state_publish(self):
        text = self.workflow()
        self.assertIn("name: Poll journal project mailbox", text)
        self.assertIn('cron: "7 */2 * * *"', text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("workflow_run:", text)
        self.assertIn("- Auto-approve PR", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("contents: write", text)
        self.assertNotIn("issues: write", text)
        self.assertNotIn("models: read", text)
        self.assertIn("group: journal-data-update", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("readonly project mailbox", text.casefold())

    def test_workflow_restores_data_truth_before_polling(self):
        text = self.workflow()
        restore = text.index("Restore current data truth")
        baseline = text.index("Snapshot mailbox state baseline")
        poll = text.index("Poll dedicated project mailbox")
        self.assertLess(restore, baseline)
        self.assertLess(baseline, poll)
        self.assertIn("git fetch origin data", text)
        self.assertIn("git archive origin/data public/api | tar -x", text)
        self.assertIn("origin/data:data/monitoring/state.json", text)

    def test_workflow_uses_project_mailbox_secrets_without_exposing_values(self):
        text = self.workflow()
        for name in (
            "IMAP_HOST",
            "IMAP_PORT",
            "IMAP_USERNAME",
            "IMAP_PASSWORD",
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
        ):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", text)
        self.assertIn("python scripts/mailbox_announcements.py", text)
        self.assertNotIn("set -x", text)

    def test_state_publish_is_delta_only_and_never_stages_public_data(self):
        text = self.workflow()
        publish = text.split("- name: Publish mailbox announcement state", 1)[1]
        self.assertIn("for attempt in 1 2 3 4 5", publish)
        self.assertIn("git fetch origin data", publish)
        self.assertIn("publish_data_delta.py apply", publish)
        self.assertIn("--path data/monitoring/state.json", publish)
        self.assertNotIn("--path public/api", publish)
        self.assertNotRegex(publish, r"git .*add .*public/api")
        self.assertIn('git -C "$data_tree" add -f data/monitoring/state.json', publish)
        self.assertIn("Update journal mailbox announcement state", publish)

    def test_mailbox_reader_has_no_mutating_imap_commands(self):
        text = READER.read_text(encoding="utf-8")
        self.assertIn('select("INBOX", readonly=True)', text)
        self.assertIn('"(BODY.PEEK[])"', text)
        for forbidden in (
            "client.store(",
            "client.expunge(",
            "client.delete(",
            "client.rename(",
            "client.append(",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
