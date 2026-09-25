from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate_history_restat_batch_dispatch.py"
TRIGGER = ROOT / ".github/workflows/controlled-history-trigger.yml"


class RestatBatchDispatchTests(unittest.TestCase):
    def _event(
        self,
        *,
        body: str = "/history-restat-batch restat-r3-2023-2025",
        actor: str = "SIMON-WORLD",
        issue_number: int = 205,
    ) -> dict[str, object]:
        return {
            "action": "created",
            "issue": {"number": issue_number},
            "comment": {"body": body, "user": {"login": actor}},
        }

    def _bundle(self, root: Path, *, include_evidence: bool = True) -> None:
        rel = Path("data/provenance/official-rosters/restat/restat-105-5.json")
        manifest = root / "data/provenance/official-evidence-batches/restat-r3-2023-2025.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "batch_id": "restat-r3-2023-2025",
                    "publisher_family": "mit-press-direct",
                    "journal_id": "restat",
                    "method": "official-page-read",
                    "finalized": True,
                    "capture_reference": "parallel-search-extract:fixture",
                    "issue_ids": ["restat-105-5"],
                    "evidence_paths": [str(rel)],
                }
            ),
            encoding="utf-8",
        )
        if include_evidence:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "capture_mode": "official-roster-evidence",
                        "method": "official-page-read",
                        "captured_at": "2026-09-24T00:00:00Z",
                        "finalized": True,
                        "capture_reference": "parallel-search-extract:fixture",
                        "journal_id": "restat",
                        "issue_id": "restat-105-5",
                        "official_url": "https://direct.mit.edu/rest/issue/105/5",
                        "allow_archive_reorder": True,
                        "excluded_item_count": 0,
                        "excluded_items": [],
                        "items": [
                            {
                                "sequence": 1,
                                "doi": "10.1162/rest_a_01174",
                                "title_en": "When Work Moves",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

    def _run(self, root: Path, event: dict[str, object]) -> subprocess.CompletedProcess[str]:
        event_path = root / "event.json"
        output = root / "output.txt"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(event_path),
                "--repo-root",
                str(root),
                "--github-output",
                str(output),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_exact_batch_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._bundle(root)
            result = self._run(root, self._event())
            self.assertEqual(0, result.returncode, result.stderr)
            output = (root / "output.txt").read_text(encoding="utf-8")
            self.assertIn("batch_id=restat-r3-2023-2025", output)
            self.assertIn("issue_ids=restat-105-5", output)

    def test_wrong_actor_issue_and_shape_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._bundle(root)
            for event in (
                self._event(actor="someone-else"),
                self._event(issue_number=265),
                self._event(body="/history-restat-batch restat-r3-2023-2025 extra"),
            ):
                with self.subTest(event=event):
                    self.assertNotEqual(0, self._run(root, event).returncode)

    def test_missing_or_non_mit_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._bundle(root, include_evidence=False)
            self.assertNotEqual(0, self._run(root, self._event()).returncode)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._bundle(root)
            path = root / "data/provenance/official-rosters/restat/restat-105-5.json"
            evidence = json.loads(path.read_text(encoding="utf-8"))
            evidence["official_url"] = "https://example.com/rest/issue/105/5"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            self.assertNotEqual(0, self._run(root, self._event()).returncode)

    def test_workflow_has_controlled_restat_route(self) -> None:
        text = TRIGGER.read_text(encoding="utf-8")
        self.assertIn("validate_restat_batch:", text)
        self.assertIn("scripts/validate_history_restat_batch_dispatch.py", text)
        self.assertIn("startsWith(github.event.comment.body, '/history-restat-batch ')", text)
        self.assertIn("restat_batch:", text)
        self.assertIn("needs: validate_restat_batch", text)
        self.assertIn(
            "evidence_issue_ids: " + "$" + "{{ needs.validate_restat_batch.outputs.issue_ids }}",
            text,
        )


if __name__ == "__main__":
    unittest.main()
