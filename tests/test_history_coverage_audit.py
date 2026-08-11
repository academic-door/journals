from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.audit_history_coverage import audit_history_integrity
from scripts.backfill_history import COLLECTOR_REVISION
from scripts.update_journals import normalize_issue_content, stamp_issue_readiness


def ready_issue() -> dict:
    issue = {
        "schema_version": "1.0",
        "issue_id": "aer-114-1",
        "journal_id": "aer",
        "journal_name": "American Economic Review",
        "volume": "114",
        "issue": "1",
        "source_url": "https://www.aeaweb.org/issues/700",
        "retrieved_at": "2026-08-11T00:00:00+00:00",
        "expected_article_count": 1,
        "research_article_count": 1,
        "status": "ready",
        "articles": [
            {
                "paper_id": "doi:10.1/demo",
                "sequence": 1,
                "article_type": "research-article",
                "doi": "10.1/demo",
                "title_en": "English title",
                "title_cn": "中文标题",
                "authors": ["A. Author"],
                "abstract_en": "English abstract.",
                "abstract_cn": "中文摘要。",
                "source_url": "https://www.aeaweb.org/articles?id=10.1/demo",
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
    return stamp_issue_readiness(normalize_issue_content(issue))


class HistoryCoverageAuditTests(unittest.TestCase):
    def test_script_entrypoint_resolves_repo_imports(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "audit_history_coverage.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)

    def _fixture(self, root: Path) -> tuple[list[dict], dict, Path]:
        api_root = root / "api"
        issue_dir = api_root / "journals" / "aer" / "issues"
        issue_dir.mkdir(parents=True)
        issue = ready_issue()
        (issue_dir / "aer-114-1.json").write_text(
            json.dumps(issue), encoding="utf-8"
        )
        (issue_dir / "index.json").write_text(
            json.dumps(
                {
                    "issues": [
                        {
                            "issue_id": "aer-114-1",
                            "content_status": "complete",
                            "source_status": "official_verified",
                            "publication_state": "ready",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        state = {
            "schema_version": "1.1",
            "issues": {
                "aer-114-1": {
                    "journal": "AER",
                    "year": 2024,
                    "volume": "114",
                    "issue": "1",
                    "status": "ready",
                    "content_status": "complete",
                    "source_status": "official_verified",
                    "publication_state": "ready",
                }
            },
            "discovery": {
                "AER": {
                    "issue_ids": ["aer-114-1"],
                    "issue_years": {"aer-114-1": 2024},
                    "authority": "official_archive",
                    "refreshed_at": "2026-08-11T00:00:00+00:00",
                    "collector_revision": COLLECTOR_REVISION,
                }
            },
        }
        return [state], {"AER": {"id": "aer", "name": "AER"}}, api_root

    def test_four_way_consistency_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            states, journals, api_root = self._fixture(Path(directory))
            report = audit_history_integrity(
                states, journals=journals, api_root=api_root
            )
        self.assertEqual("pass", report["status"])
        self.assertEqual(1, report["counts"]["publication_ready"])

    def test_missing_index_and_archive_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            states, journals, api_root = self._fixture(Path(directory))
            issue_dir = api_root / "journals" / "aer" / "issues"
            (issue_dir / "aer-114-1.json").unlink()
            (issue_dir / "index.json").write_text(
                json.dumps({"issues": []}), encoding="utf-8"
            )
            report = audit_history_integrity(
                states, journals=journals, api_root=api_root
            )
        self.assertEqual("fail", report["status"])
        joined = "\n".join(report["errors"])
        self.assertIn("archive missing", joined)
        self.assertIn("archive index entry missing", joined)
        self.assertIn("state publication ready != archive blocked", joined)


if __name__ == "__main__":
    unittest.main()
