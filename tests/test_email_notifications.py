from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.email_notifications import (
    SMTPSettings,
    build_message,
    send_test_notification,
    synchronize,
)


def write_issue(
    root: Path,
    journal: str,
    issue_id: str,
    papers: list[str],
    *,
    filename: str = "current.json",
    abstracts: int | None = None,
    translations: int | None = None,
) -> None:
    path = root / journal / "issues" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    issue = {
        "status": "ready",
        "issue_id": issue_id,
        "journal_id": journal,
        "journal_name": "American Economic Review",
        "volume": "116",
        "issue": "8",
        "publication_date": "August 2026",
        "research_article_count": len(papers),
        "quality": {
            "abstract_en_complete": (
                len(papers) if abstracts is None else abstracts
            ),
            "translation_complete": (
                len(papers) if translations is None else translations
            ),
        },
        "articles": [
            {
                "paper_id": paper,
                "title_en": "Evidence from China" if index == 0 else "Other paper",
                "title_cn": "来自中国的证据" if index == 0 else "其他论文",
                "authors": ["Author"],
                "abstract_en": "We study firms in China." if index == 0 else "Other.",
                "abstract_cn": "",
                "doi": paper.removeprefix("doi:"),
                "source_url": f"https://doi.org/{paper.removeprefix('doi:')}",
            }
            for index, paper in enumerate(papers)
        ],
    }
    path.write_text(json.dumps(issue), encoding="utf-8")


def settings() -> SMTPSettings:
    return SMTPSettings(
        host="smtp.example.test",
        port=587,
        security="starttls",
        username="sender@example.test",
        password="not-a-real-password",
        sender="sender@example.test",
        recipients=("owner@example.test",),
    )


class EmailNotificationTests(unittest.TestCase):
    def test_manual_test_email_does_not_touch_notification_state(self):
        messages = []
        send_test_notification(
            settings(),
            transport=lambda message, smtp: messages.append(message),
        )
        self.assertEqual(1, len(messages))
        self.assertIn("测试成功", str(messages[0]["Subject"]))
        self.assertIn("成功连接发件邮箱", messages[0].get_body().get_content())

    def test_manual_issue_preview_uses_the_real_notification_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            public = Path(temporary)
            write_issue(public, "aer", "aer-116-8", ["doi:10.1/one"])
            issue = json.loads(
                (public / "aer" / "issues" / "current.json").read_text(
                    encoding="utf-8"
                )
            )
            messages = []
            send_test_notification(
                settings(),
                issue=issue,
                transport=lambda message, smtp: messages.append(message),
            )
        self.assertEqual(1, len(messages))
        self.assertIn("[测试预览]", str(messages[0]["Subject"]))
        body = messages[0].get_body().get_content()
        self.assertIn("American Economic Review", body)
        self.assertIn("中国相关 1 篇", body)
        self.assertIn("打开微信公众号编辑器", body)

    def test_incomplete_but_published_issue_is_in_notification_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "jde", "jde-182", ["doi:10.1/one"])
            path = public / "jde" / "issues" / "current.json"
            issue = json.loads(path.read_text(encoding="utf-8"))
            issue["status"] = "incomplete"
            path.write_text(json.dumps(issue), encoding="utf-8")
            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual("seeded", outcome["status"])
            self.assertIn("jde", saved["known_ready"])

    def test_old_partial_baseline_is_reseeded_without_bulk_email(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-7", ["doi:10.1/one"])
            write_issue(public, "jde", "jde-182", ["doi:10.1/two"])
            state.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "known": {"aer": {"fingerprint": "old"}},
                        "pending": {},
                        "sent": {},
                    }
                ),
                encoding="utf-8",
            )
            messages = []
            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=settings(),
                transport=lambda message, smtp: messages.append(message),
            )
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual("seeded", outcome["status"])
            self.assertEqual([], messages)
            self.assertEqual({"aer", "jde"}, set(saved["known_ready"]))
            self.assertEqual("1.2", saved["schema_version"])

    def test_version_11_state_migrates_without_losing_next_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-7", ["doi:10.1/old"])
            old_issue = json.loads(
                (public / "aer" / "issues" / "current.json").read_text(
                    encoding="utf-8"
                )
            )
            from scripts.email_notifications import _journal_collections, issue_snapshot

            old_snapshot = issue_snapshot(old_issue, _journal_collections())
            state.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "known": {"aer": old_snapshot},
                        "pending": {},
                        "sent": {},
                    }
                ),
                encoding="utf-8",
            )
            write_issue(
                public,
                "aer",
                "aer-116-8",
                ["doi:10.1/new"],
                filename="detected.json",
                abstracts=0,
                translations=0,
            )
            messages = []
            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="skipped",
                settings=settings(),
                transport=lambda message, smtp: messages.append(message),
            )
            self.assertEqual("sent", outcome["status"])
            self.assertIn("发现新卷期", str(messages[0]["Subject"]))
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual("1.2", saved["schema_version"])
            self.assertEqual("aer-116-8", saved["known_detected"]["aer"]["issue_id"])

    def test_workflows_reuse_the_published_data_worktree_for_email_state(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("monitor-journals.yml", "update-journals.yml"):
            source = (root / ".github" / "workflows" / name).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("email-state-tree", source)
            self.assertIn('data_tree="$RUNNER_TEMP/data-tree"', source)
            persist = source.split(
                "      - name: Persist private email notification state", 1
            )[1].split("      - name: Open or close private email failure alert", 1)[0]
            self.assertNotIn('git worktree add "$data_tree" origin/data', persist)
            self.assertNotIn('git -C "$data_tree" switch -C data', persist)

    def test_monitor_does_not_run_a_redundant_all_journal_retry(self):
        root = Path(__file__).resolve().parents[1]
        source = (
            root / ".github" / "workflows" / "monitor-journals.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("--enrich-detected", source)
        self.assertNotIn("--journal ALL", source)
        self.assertIn("--run-updates --translate", source)

    def test_script_entrypoint_runs_without_pythonpath(self):
        result = subprocess.run(
            [sys.executable, "scripts/email_notifications.py", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_first_run_seeds_without_email(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-7", ["doi:10.1/one"])
            sent = []
            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=settings(),
                transport=lambda message, smtp: sent.append(message),
            )
            self.assertEqual("seeded", outcome["status"])
            self.assertEqual([], sent)
            self.assertIn("aer", json.loads(state.read_text())["known_ready"])

    def test_new_issue_waits_for_composer_then_sends_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-7", ["doi:10.1/one"])
            synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )
            write_issue(public, "aer", "aer-116-8", ["doi:10.1/two"])
            blocked = synchronize(
                public_root=public,
                state_path=state,
                composer_status="failure",
                settings=settings(),
            )
            self.assertEqual("waiting_for_composer", blocked["status"])
            self.assertEqual(1, blocked["queued"])

            messages = []
            delivered = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=settings(),
                transport=lambda message, smtp: messages.append(message),
            )
            self.assertEqual("sent", delivered["status"])
            self.assertEqual(["aer"], delivered["journals"])
            self.assertIn("中国相关 1 篇", messages[0].get_body().get_content())

            duplicate = synchronize(
                public_root=public,
                state_path=state,
                composer_status="skipped",
                settings=settings(),
                transport=lambda message, smtp: messages.append(message),
            )
            self.assertEqual("idle", duplicate["status"])
            self.assertEqual(1, len(messages))

    def test_same_issue_new_article_is_a_supplement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-8", ["doi:10.1/one"])
            synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )
            write_issue(
                public,
                "aer",
                "aer-116-8",
                ["doi:10.1/one", "doi:10.1/two"],
            )
            messages = []
            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=settings(),
                transport=lambda message, smtp: messages.append(message),
            )
            self.assertEqual("sent", outcome["status"])
            self.assertIn("本期补录", messages[0].get_body().get_content())

    def test_unconfigured_keeps_pending_for_a_later_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-7", ["doi:10.1/one"])
            synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )
            write_issue(public, "aer", "aer-116-8", ["doi:10.1/two"])
            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )
            self.assertEqual("unconfigured", outcome["status"])
            self.assertIn("aer", json.loads(state.read_text())["pending_ready"])

    def test_failure_does_not_print_or_store_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-7", ["doi:10.1/one"])
            synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )
            write_issue(public, "aer", "aer-116-8", ["doi:10.1/two"])

            def fail(_message, _settings):
                raise RuntimeError("provider rejected password")

            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=settings(),
                transport=fail,
            )
            self.assertEqual(
                {
                    "status",
                    "queued",
                    "sent",
                    "journals",
                    "messages",
                    "error_type",
                },
                set(outcome),
            )
            self.assertNotIn("password", state.read_text())

    def test_settings_accept_starttls_and_multiple_recipients(self):
        environ = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "2525",
            "SMTP_SECURITY": "starttls",
            "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "placeholder",
            "SMTP_FROM": "Academic Door <sender@example.test>",
            "NOTIFICATION_EMAIL_TO": "one@example.test;two@example.test",
        }
        with patch.dict(os.environ, environ, clear=True):
            configured = SMTPSettings.from_environment()
        self.assertIsNotNone(configured)
        self.assertEqual(2525, configured.port)
        self.assertEqual(
            ("one@example.test", "two@example.test"),
            configured.recipients,
        )

    def test_empty_optional_security_secret_uses_starttls_default(self):
        environ = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_SECURITY": "",
            "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "placeholder",
            "NOTIFICATION_EMAIL_TO": "owner@example.test",
        }
        with patch.dict(os.environ, environ, clear=True):
            configured = SMTPSettings.from_environment()
        self.assertIsNotNone(configured)
        self.assertEqual("starttls", configured.security)
        self.assertEqual(587, configured.port)

    def test_message_combines_multiple_journals(self):
        events = [
            {
                "journal_name": "Journal One",
                "event_type": "new_issue",
                "issue_label": "Vol. 1 · No. 1",
                "publication_date": "July 2026",
                "article_count": 10,
                "china_related_count": 2,
                "directory_url": "https://example.test/one",
                "composer_url": "https://example.test/composer/one",
            },
            {
                "journal_name": "Journal Two",
                "event_type": "supplement",
                "issue_label": "Vol. 2",
                "publication_date": "August 2026",
                "article_count": 12,
                "china_related_count": 0,
                "directory_url": "https://example.test/two",
                "composer_url": "https://example.test/composer/two",
            },
        ]
        message = build_message(events, settings())
        self.assertIn("2 本期刊", str(message["Subject"]))
        body = message.get_body().get_content()
        self.assertIn("Journal One", body)
        self.assertIn("Journal Two", body)

    def test_detected_then_ready_sends_two_distinct_notifications(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-7", ["doi:10.1/old"])
            write_issue(
                public,
                "aer",
                "aer-116-7",
                ["doi:10.1/old"],
                filename="detected.json",
            )
            synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )

            write_issue(
                public,
                "aer",
                "aer-116-8",
                ["doi:10.1/new-one", "doi:10.1/new-two"],
                filename="detected.json",
                abstracts=1,
                translations=0,
            )
            messages = []
            detected = synchronize(
                public_root=public,
                state_path=state,
                composer_status="skipped",
                settings=settings(),
                transport=lambda message, smtp: messages.append(message),
            )
            self.assertEqual("sent", detected["status"])
            self.assertEqual(1, detected["messages"])
            self.assertIn("发现新卷期", str(messages[0]["Subject"]))
            detected_body = messages[0].get_body().get_content()
            self.assertIn("等待英文摘要 · 1/2", detected_body)
            self.assertNotIn("打开微信公众号编辑器", detected_body)

            write_issue(
                public,
                "aer",
                "aer-116-8",
                ["doi:10.1/new-one", "doi:10.1/new-two"],
            )
            write_issue(
                public,
                "aer",
                "aer-116-8",
                ["doi:10.1/new-one", "doi:10.1/new-two"],
                filename="detected.json",
            )
            ready = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=settings(),
                transport=lambda message, smtp: messages.append(message),
            )
            self.assertEqual("sent", ready["status"])
            self.assertEqual(1, ready["messages"])
            self.assertEqual(2, len(messages))
            self.assertIn("新卷期已就绪", str(messages[1]["Subject"]))
            self.assertIn(
                "打开微信公众号编辑器",
                messages[1].get_body().get_content(),
            )

    def test_same_run_complete_issue_only_sends_ready_notification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            write_issue(public, "aer", "aer-116-7", ["doi:10.1/old"])
            synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )
            write_issue(public, "aer", "aer-116-8", ["doi:10.1/new"])
            write_issue(
                public,
                "aer",
                "aer-116-8",
                ["doi:10.1/new"],
                filename="detected.json",
            )
            messages = []
            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=settings(),
                transport=lambda message, smtp: messages.append(message),
            )
            self.assertEqual("sent", outcome["status"])
            self.assertEqual(1, outcome["messages"])
            self.assertEqual(1, len(messages))
            self.assertIn("新卷期已就绪", str(messages[0]["Subject"]))


if __name__ == "__main__":
    unittest.main()
