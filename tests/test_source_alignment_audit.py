from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.audit_source_alignment import build_audit


ROOT = Path(__file__).resolve().parents[1]


class SourceAlignmentAuditTests(unittest.TestCase):
    def test_reports_official_counts_and_fallback_without_false_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "journals.yml"
            config.write_text(
                """journals:
  DEMO:
    id: demo
    short_name: Demo
    name: Demo Journal
    publisher: Demo
    current_issue_url: https://publisher.example/current
    enabled: true
""",
                encoding="utf-8",
            )
            issue_root = root / "public" / "demo" / "issues"
            issue_root.mkdir(parents=True)
            issue = {
                "issue_id": "demo-1-1",
                "publication_date": "July 2026",
                "publication_state": "ready",
                "source_url": "https://publisher.example/current",
                "research_article_count": 1,
                "articles": [{}],
                "content_counts": {
                    "official_items": 3,
                    "observed_items": 3,
                    "publishable_items": 1,
                },
                "quality": {
                    "roster_match": True,
                    "roster_authority": "publisher-rss",
                    "roster_transport": "publisher-rss",
                    "excluded_item_count": 2,
                    "excluded_items": [{}, {}],
                    "abstract_en_complete": 1,
                    "translation_complete": 1,
                    "flags": ["official_order_unverified"],
                },
            }
            for name in ("current.json", "detected.json"):
                (issue_root / name).write_text(json.dumps(issue), encoding="utf-8")
            payload = build_audit(config_path=config, public_root=root / "public")
        self.assertEqual("healthy", payload["status"])
        self.assertEqual(1, payload["summary"]["publisher_feed_verified"])
        self.assertEqual(0, payload["summary"]["invariant_errors"])
        self.assertFalse(payload["journals"][0]["detected"]["order_verified"])


    def test_repository_audit_covers_every_enabled_journal(self) -> None:
        payload = build_audit()
        journal_ids = [item["journal_id"] for item in payload["journals"]]
        self.assertEqual(49, payload["summary"]["configured_journals"])
        self.assertEqual(49, len(journal_ids))
        self.assertEqual(49, len(set(journal_ids)))
        self.assertIn("aer", journal_ids)
        self.assertIn("jeem", journal_ids)
        self.assertIn("wd", journal_ids)
        self.assertIn("jhe", journal_ids)
        self.assertIn("jce", journal_ids)
        self.assertIn("red", journal_ids)

if __name__ == "__main__":
    unittest.main()
