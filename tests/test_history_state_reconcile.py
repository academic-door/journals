from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.history_state_reconcile import reconcile_state_file
from scripts.reconcile_missing_history_state import reconcile_missing_ready
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


class HistoryStateReconcileTests(unittest.TestCase):
    def _fixture(self, root: Path, *, index_source_status: str = "official_verified") -> tuple[Path, Path, Path, dict]:
        api_root = root / "api"
        issue_dir = api_root / "journals" / "aer" / "issues"
        issue_dir.mkdir(parents=True)
        issue = ready_issue()
        archive_path = issue_dir / "aer-114-1.json"
        archive_path.write_text(json.dumps(issue), encoding="utf-8")
        index_path = issue_dir / "index.json"
        index_path.write_text(
            json.dumps(
                {
                    "issues": [
                        {
                            "issue_id": "aer-114-1",
                            "content_status": "complete",
                            "source_status": index_source_status,
                            "publication_state": "ready",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        state_path = root / "field-2023-2026.json"
        state = {
            "schema_version": "1.1",
            "updated_at": "2026-09-01T00:00:00+00:00",
            "issues": {
                "aer-114-1": {
                    "journal": "AER",
                    "year": 2024,
                    "volume": "114",
                    "issue": "1",
                    "official_url": "https://www.aeaweb.org/issues/700",
                    "status": "blocked",
                    "content_status": "blocked",
                    "source_status": "source_pending",
                    "publication_state": "blocked",
                    "last_error": "archive_missing",
                    "retry_class": "transient",
                    "attempt_count": 3,
                    "last_attempt_at": "2026-09-01T00:00:00+00:00",
                    "next_retry_at": "2026-09-01T02:00:00+00:00",
                }
            },
            "discovery": {"AER": {"issue_ids": ["aer-114-1"]}},
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        journals = {"AER": {"id": "aer", "name": "AER"}}
        return state_path, archive_path, index_path, journals

    def _remove_existing_state_but_keep_official_discovery(self, state_path: Path) -> None:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["issues"] = {}
        state["discovery"]["AER"].update(
            {
                "authority": "official_archive",
                "refreshed_at": "2026-09-01T00:00:00+00:00",
                "issue_years": {"aer-114-1": 2024},
                "issue_refs": {
                    "aer-114-1": {
                        "journal": "AER",
                        "year": 2024,
                        "volume": "114",
                        "issue": "1",
                        "official_url": "https://www.aeaweb.org/issues/700",
                    }
                },
            }
        )
        state_path.write_text(json.dumps(state), encoding="utf-8")

    def test_recovers_missing_ready_operational_row_without_fabricating_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, archive_path, index_path, journals = self._fixture(root)
            self._remove_existing_state_but_keep_official_discovery(state_path)
            archive_before, index_before = archive_path.read_bytes(), index_path.read_bytes()
            report = reconcile_missing_ready(
                [state_path], journals=journals, api_root=root / "api"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            entry = state["issues"]["aer-114-1"]
            self.assertEqual(1, report["created_count"])
            self.assertEqual("ready", entry["publication_state"])
            self.assertEqual("official_verified", entry["source_status"])
            self.assertEqual("canonical_archive_and_index", entry["operational_state_recovered_from"])
            for forbidden in ("attempt_count", "last_attempt_at", "refreshed_at", "authority_observed_at"):
                self.assertNotIn(forbidden, entry)
            self.assertEqual(archive_before, archive_path.read_bytes())
            self.assertEqual(index_before, index_path.read_bytes())
            repeated = reconcile_missing_ready([state_path], journals=journals, api_root=root / "api")
            self.assertEqual(0, repeated["created_count"])

    def test_missing_ready_recovery_fails_closed_on_disagreeing_archive_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, _, _, journals = self._fixture(root, index_source_status="source_pending")
            self._remove_existing_state_but_keep_official_discovery(state_path)
            prior = state_path.read_bytes()
            report = reconcile_missing_ready([state_path], journals=journals, api_root=root / "api")
            self.assertEqual(0, report["created_count"])
            self.assertEqual("archive_index_disagree", report["skipped"]["aer-114-1"])
            self.assertEqual(prior, state_path.read_bytes())

    def test_missing_ready_recovery_does_not_promote_candidate_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, _, _, journals = self._fixture(root)
            self._remove_existing_state_but_keep_official_discovery(state_path)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["discovery"]["AER"]["authority"] = "crossref_candidate"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            report = reconcile_missing_ready([state_path], journals=journals, api_root=root / "api")
            self.assertEqual(0, report["created_count"])
            self.assertEqual("unqualified_discovery_identity", report["skipped"]["aer-114-1"])

    def test_missing_ready_recovery_rejects_ambiguous_discovery_state_owners(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, _, _, journals = self._fixture(root)
            self._remove_existing_state_but_keep_official_discovery(state_path)
            duplicate = root / "second.json"
            duplicate.write_bytes(state_path.read_bytes())
            report = reconcile_missing_ready(
                [state_path, duplicate], journals=journals, api_root=root / "api"
            )
            self.assertEqual(0, report["created_count"])
            self.assertEqual("ambiguous_discovery_owner", report["skipped"]["aer-114-1"])

    def test_recovery_script_starts_as_a_standalone_actions_command(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "scripts/reconcile_missing_history_state.py", "--help"],
            cwd=root, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--api-root", result.stdout)

    def test_reconciles_stale_state_when_archive_and_index_agree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, archive_path, index_path, journals = self._fixture(root)
            archive_before = archive_path.read_bytes()
            index_before = index_path.read_bytes()

            result = reconcile_state_file(
                state_path,
                journals=journals,
                api_root=root / "api",
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            entry = state["issues"]["aer-114-1"]

            self.assertEqual(1, result["changed_count"])
            self.assertEqual("ready", entry["status"])
            self.assertEqual("ready", entry["publication_state"])
            self.assertEqual("complete", entry["content_status"])
            self.assertEqual("official_verified", entry["source_status"])
            self.assertEqual("", entry["last_error"])
            self.assertNotIn("next_retry_at", entry)
            self.assertEqual(3, entry["attempt_count"])
            self.assertEqual("2026-09-01T00:00:00+00:00", entry["last_attempt_at"])
            self.assertEqual("https://www.aeaweb.org/issues/700", entry["official_url"])
            self.assertEqual(archive_before, archive_path.read_bytes())
            self.assertEqual(index_before, index_path.read_bytes())

    def test_skips_when_index_does_not_agree_with_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, _, _, journals = self._fixture(
                root, index_source_status="source_pending"
            )
            before = state_path.read_bytes()
            result = reconcile_state_file(
                state_path,
                journals=journals,
                api_root=root / "api",
            )
            self.assertEqual(0, result["changed_count"])
            self.assertEqual(before, state_path.read_bytes())

    def test_skips_missing_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, archive_path, _, journals = self._fixture(root)
            archive_path.unlink()
            before = state_path.read_bytes()
            result = reconcile_state_file(
                state_path,
                journals=journals,
                api_root=root / "api",
            )
            self.assertEqual(0, result["changed_count"])
            self.assertEqual(before, state_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
