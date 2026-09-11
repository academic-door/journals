from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.history_state_reconcile import reconcile_state_file


def ready_issue(issue_id: str, issue: str) -> dict:
    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": "aer",
        "journal_name": "American Economic Review",
        "volume": "114",
        "issue": issue,
        "publication_date": "2024",
        "source_url": f"https://www.aeaweb.org/issues/{issue}",
        "retrieved_at": "2026-09-11T00:00:00+00:00",
        "expected_article_count": 1,
        "research_article_count": 1,
        "status": "ready",
        "content_status": "complete",
        "source_status": "official_verified",
        "publication_state": "ready",
        "articles": [
            {
                "paper_id": f"doi:10.1/{issue}",
                "sequence": 1,
                "source_sequence": 1,
                "article_type": "research-article",
                "doi": f"10.1/{issue}",
                "title_en": "English title",
                "title_cn": "中文标题",
                "authors": ["A. Author"],
                "abstract_en": "English abstract.",
                "abstract_cn": "中文摘要。",
                "source_url": f"https://www.aeaweb.org/articles?id=10.1/{issue}",
                "publication_date": "2024",
                "sources": {},
                "translation": {"status": "complete"},
                "quality_flags": [],
            }
        ],
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_authority": "official-issue-page",
            "roster_transport": "official-issue-page",
            "doi_complete": 1,
            "authors_complete": 1,
            "abstract_en_complete": 1,
            "translation_complete": 1,
            "duplicate_count": 0,
            "flags": [],
        },
    }


def stale_entry(issue: str) -> dict:
    return {
        "journal": "AER",
        "year": 2024,
        "volume": "114",
        "issue": issue,
        "official_url": f"https://www.aeaweb.org/issues/{issue}",
        "status": "blocked",
        "content_status": "blocked",
        "source_status": "source_pending",
        "publication_state": "blocked",
        "last_error": "archive_missing",
        "retry_class": "transient",
        "attempt_count": 1,
        "last_attempt_at": "2026-09-01T00:00:00+00:00",
        "next_retry_at": "2026-09-01T02:00:00+00:00",
    }


class HistoryStateReconcileScopeTests(unittest.TestCase):
    def test_issue_filter_reconciles_only_requested_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issue_dir = root / "api" / "journals" / "aer" / "issues"
            issue_dir.mkdir(parents=True)
            issue_ids = ("aer-114-1", "aer-114-2")
            for issue_id, issue in zip(issue_ids, ("1", "2")):
                (issue_dir / f"{issue_id}.json").write_text(
                    json.dumps(ready_issue(issue_id, issue)), encoding="utf-8"
                )
            (issue_dir / "index.json").write_text(
                json.dumps(
                    {
                        "issues": [
                            {
                                "issue_id": issue_id,
                                "content_status": "complete",
                                "source_status": "official_verified",
                                "publication_state": "ready",
                            }
                            for issue_id in issue_ids
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state_path = root / "field-2023-2026.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "issues": {
                            "aer-114-1": stale_entry("1"),
                            "aer-114-2": stale_entry("2"),
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = reconcile_state_file(
                state_path,
                journals={"AER": {"id": "aer", "name": "AER"}},
                api_root=root / "api",
                issue_ids={"aer-114-1"},
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(1, result["changed_count"])
            self.assertEqual("ready", state["issues"]["aer-114-1"]["status"])
            self.assertEqual("blocked", state["issues"]["aer-114-2"]["status"])
            self.assertIn("next_retry_at", state["issues"]["aer-114-2"])


if __name__ == "__main__":
    unittest.main()
