from __future__ import annotations

from email.message import EmailMessage
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "mailbox_announcements.py"


def _message_bytes(
    *,
    sender: str = "alerts@journals.uchicago.edu",
    subject: str = "Journal of Political Economy — Volume 134, Issue 9 — September 2026",
    body: str = (
        "Journal of Political Economy Volume 134, Issue 9, September 2026\n"
        "https://www.journals.uchicago.edu/toc/jpe/134/9\n"
    ),
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "academic-door@163.com"
    message["Date"] = "Thu, 17 Sep 2026 12:00:00 +0000"
    message["Message-ID"] = "<jpe-134-9@example.invalid>"
    message["Subject"] = subject
    message.set_content(body)
    return message.as_bytes()


JPE_RULE = {
    "journal_key": "JPE",
    "journal_id": "jpe",
    "sender_domains": ["journals.uchicago.edu", "uchicago.edu"],
    "subject_patterns": [r"Journal of Political Economy"],
    "official_link_hosts": ["journals.uchicago.edu"],
}


class FakeIMAP:
    def __init__(self, _host: str, _port: int, messages: list[bytes] | None = None):
        self.messages = list(messages or [])
        self.readonly = None
        self.fetch_specs: list[str] = []
        self.logged_in = False
        self.logged_out = False

    def login(self, _username: str, _password: str):
        self.logged_in = True
        return "OK", [b"logged in"]

    def select(self, _mailbox: str, readonly: bool = False):
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def search(self, _charset, *_criteria):
        ids = b" ".join(str(index + 1).encode() for index in range(len(self.messages)))
        return "OK", [ids]

    def fetch(self, message_id: bytes, spec: str):
        self.fetch_specs.append(spec)
        index = int(message_id) - 1
        return "OK", [(b"RFC822", self.messages[index])]

    def logout(self):
        self.logged_out = True
        return "BYE", [b"logout"]


class MailboxAnnouncementTests(unittest.TestCase):
    def module(self):
        try:
            spec = importlib.util.spec_from_file_location("mailbox_announcements", MODULE_PATH)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except FileNotFoundError:
            self.fail("scripts/mailbox_announcements.py is not implemented")

    def test_settings_reuse_163_project_mailbox_credentials(self):
        module = self.module()
        settings = module.MailboxSettings.from_environment(
            {
                "SMTP_USERNAME": "academic-door@163.com",
                "SMTP_PASSWORD": "project-auth-code",
            }
        )
        self.assertIsNotNone(settings)
        self.assertEqual(settings.host, "imap.163.com")
        self.assertEqual(settings.port, 993)
        self.assertEqual(settings.username, "academic-door@163.com")

    def test_valid_official_newsletter_becomes_announcement_only_signal(self):
        module = self.module()
        signal = module.extract_signal(_message_bytes(), JPE_RULE)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["journal_id"], "jpe")
        self.assertEqual(signal["issue_id"], "jpe-134-9")
        self.assertEqual(signal["volume"], "134")
        self.assertEqual(signal["issue"], "9")
        self.assertEqual(signal["publication_date"], "September 2026")
        self.assertEqual(signal["publication_state"], "announced")
        self.assertEqual(signal["source_authority"], "first_party")
        self.assertEqual(signal["source_kind"], "official_newsletter")
        self.assertEqual(
            signal["source_url"],
            "https://www.journals.uchicago.edu/toc/jpe/134/9",
        )
        self.assertNotIn("body", signal)
        self.assertNotIn("subject", signal)

    def test_sender_subject_and_official_link_allowlists_fail_closed(self):
        module = self.module()
        self.assertIsNone(
            module.extract_signal(
                _message_bytes(sender="attacker@example.com"),
                JPE_RULE,
            )
        )
        self.assertIsNone(
            module.extract_signal(
                _message_bytes(subject="Your weekly research digest"),
                JPE_RULE,
            )
        )
        self.assertIsNone(
            module.extract_signal(
                _message_bytes(
                    body=(
                        "Journal of Political Economy Volume 134, Issue 9, September 2026\n"
                        "https://example.com/toc/jpe/134/9\n"
                    )
                ),
                JPE_RULE,
            )
        )

    def test_ambiguous_issue_identity_is_rejected(self):
        module = self.module()
        body = (
            "Journal of Political Economy Volume 134, Issue 9, September 2026\n"
            "Journal of Political Economy Volume 135, Issue 1, January 2027\n"
            "https://www.journals.uchicago.edu/toc/jpe/134/9\n"
        )
        self.assertIsNone(module.extract_signal(_message_bytes(body=body), JPE_RULE))

    def test_imap_scan_is_read_only_and_uses_body_peek(self):
        module = self.module()
        fake = FakeIMAP("imap.163.com", 993, [_message_bytes()])
        settings = module.MailboxSettings(
            host="imap.163.com",
            port=993,
            username="academic-door@163.com",
            password="project-auth-code",
        )
        signals = module.scan_mailbox(
            settings,
            [JPE_RULE],
            imap_factory=lambda host, port: fake,
        )
        self.assertEqual(len(signals), 1)
        self.assertTrue(fake.logged_in)
        self.assertTrue(fake.readonly)
        self.assertTrue(fake.logged_out)
        self.assertTrue(fake.fetch_specs)
        self.assertTrue(all("BODY.PEEK[]" in spec for spec in fake.fetch_specs))

    def test_apply_signal_updates_only_additive_announcement_state(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "data" / "monitoring" / "state.json"
            issue_root = root / "public" / "api" / "v1" / "journals"
            current_path = issue_root / "jpe" / "issues" / "current.json"
            detected_path = issue_root / "jpe" / "issues" / "detected.json"
            current_path.parent.mkdir(parents=True)
            state_path.parent.mkdir(parents=True)
            current = {
                "journal_id": "jpe",
                "issue_id": "jpe-134-8",
                "volume": "134",
                "issue": "8",
                "publication_date": "August 2026",
                "publication_state": "ready",
                "articles": [{"doi": "10.1086/example"}],
            }
            current_path.write_text(json.dumps(current), encoding="utf-8")
            detected_path.write_text(json.dumps(current), encoding="utf-8")
            state = {
                "schema_version": "1.0",
                "journals": {
                    "JPE": {
                        "journal_id": "jpe",
                        "status": "awaiting_official",
                        "candidate": {"issue_key": "134:9"},
                        "deep_failure_count": 31,
                    }
                },
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            signal = self.module().extract_signal(_message_bytes(), JPE_RULE)
            changed = module.apply_signals(
                state_path=state_path,
                public_root=issue_root,
                signals=[signal],
                rules=[JPE_RULE],
            )
            self.assertEqual(changed, ["JPE"])
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            entry = persisted["journals"]["JPE"]
            self.assertEqual(entry["status"], "awaiting_official")
            self.assertEqual(entry["deep_failure_count"], 31)
            self.assertEqual(entry["announcement"]["issue_id"], "jpe-134-9")
            self.assertNotIn("body", json.dumps(persisted).lower())

    def test_older_newsletter_never_overrides_ready_issue(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "data" / "monitoring" / "state.json"
            issue_root = root / "public" / "api" / "v1" / "journals"
            current_path = issue_root / "jpe" / "issues" / "current.json"
            current_path.parent.mkdir(parents=True)
            state_path.parent.mkdir(parents=True)
            current = {
                "journal_id": "jpe",
                "issue_id": "jpe-134-9",
                "volume": "134",
                "issue": "9",
                "publication_date": "September 2026",
                "publication_state": "ready",
                "articles": [{"doi": "10.1086/example"}],
            }
            current_path.write_text(json.dumps(current), encoding="utf-8")
            state_path.write_text(
                json.dumps({"schema_version": "1.0", "journals": {"JPE": {"journal_id": "jpe"}}}),
                encoding="utf-8",
            )
            old_signal = module.extract_signal(
                _message_bytes(
                    subject="Journal of Political Economy — Volume 134, Issue 8 — August 2026",
                    body=(
                        "Journal of Political Economy Volume 134, Issue 8, August 2026\n"
                        "https://www.journals.uchicago.edu/toc/jpe/134/8\n"
                    ),
                ),
                JPE_RULE,
            )
            changed = module.apply_signals(
                state_path=state_path,
                public_root=issue_root,
                signals=[old_signal],
                rules=[JPE_RULE],
            )
            self.assertEqual(changed, [])
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("announcement", persisted["journals"]["JPE"])


if __name__ == "__main__":
    unittest.main()
