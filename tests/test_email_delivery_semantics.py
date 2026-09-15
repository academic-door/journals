from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.email_notifications import SMTPSettings, synchronize


def _settings() -> SMTPSettings:
    return SMTPSettings(
        host="smtp.example.test",
        port=587,
        security="starttls",
        username="sender@example.test",
        password="placeholder",
        sender="sender@example.test",
        recipients=("owner@example.test",),
    )


def _write_issue(root: Path, issue_id: str, paper_id: str) -> None:
    path = root / "aer" / "issues" / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": "ready",
                "issue_id": issue_id,
                "journal_id": "aer",
                "journal_name": "American Economic Review",
                "volume": "116",
                "issue": issue_id.rsplit("-", 1)[-1],
                "publication_date": "September 2026",
                "research_article_count": 1,
                "quality": {
                    "abstract_en_complete": 1,
                    "translation_complete": 1,
                },
                "articles": [
                    {
                        "paper_id": paper_id,
                        "title_en": "Paper",
                        "authors": ["Author"],
                        "doi": paper_id.removeprefix("doi:"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class EmailDeliverySemanticsTests(unittest.TestCase):
    def test_success_records_smtp_acceptance_without_claiming_mailbox_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            state = root / "state.json"
            _write_issue(public, "aer-116-8", "doi:10.1/old")
            synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=None,
            )
            _write_issue(public, "aer-116-9", "doi:10.1/new")
            outcome = synchronize(
                public_root=public,
                state_path=state,
                composer_status="success",
                settings=_settings(),
                transport=lambda _message, _smtp: None,
            )
            saved = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual("smtp_accepted", outcome["delivery_status"])
        accepted = saved["sent_ready"]["aer"]
        self.assertEqual("smtp_accepted", accepted["delivery_status"])
        self.assertIn("smtp_accepted_at", accepted)
        self.assertNotIn("delivered_at", accepted)


if __name__ == "__main__":
    unittest.main()
