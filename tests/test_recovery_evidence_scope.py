from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.recovery_evidence_scope import scoped_issue_ids


class RecoveryEvidenceScopeTests(unittest.TestCase):
    def test_explicit_ids_override_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "queue.json").write_text(
                json.dumps({"issue_ids": ["old-1", "old-2"]}), encoding="utf-8"
            )
            self.assertEqual(
                ["te-20-1", "te-20-2"],
                scoped_issue_ids(root, "te-20-1,te-20-2,te-20-1"),
            )

    def test_queue_scope_is_deterministic_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "002.json").write_text(
                json.dumps({"issue_ids": ["jpe-132-3", "jpe-132-4"]}),
                encoding="utf-8",
            )
            (root / "001.json").write_text(
                json.dumps({"issue_ids": ["jpe-132-1", "jpe-132-3"]}),
                encoding="utf-8",
            )
            self.assertEqual(
                ["jpe-132-1", "jpe-132-3", "jpe-132-4"],
                scoped_issue_ids(root),
            )

    def test_invalid_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "queue.json").write_text(
                json.dumps({"issue_ids": ["../../unsafe"]}), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                scoped_issue_ids(root)


if __name__ == "__main__":
    unittest.main()
