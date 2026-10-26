"""Attested B8 source release is immutable and scoped to two issue files."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.seed_b8_jegrowth_release import (
    ARTIFACT_ID, ARTIFACT_ZIP_SHA256, EXPECTED_DOIS,
    seed_only_if_absent, verified_issue,
)


class GrowthDataSeedTests(unittest.TestCase):
    def test_direct_actions_script_invocation_resolves_repository_imports(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "scripts/seed_b8_jegrowth_release.py"), "--help"],
            cwd=root, capture_output=True, text=True, check=True,
        )
        self.assertIn("--data-root", result.stdout)

    def test_immutable_human_triggered_artifact_contract(self):
        self.assertEqual(12652145198, ARTIFACT_ID)
        self.assertEqual(64, len(ARTIFACT_ZIP_SHA256))
        self.assertEqual(3, len(set(EXPECTED_DOIS)))

    def test_digest_mismatch_fails_closed_before_unzip(self):
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            verified_issue(b"not-a-verified-zip")

    def test_only_two_approved_paths_created_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = {"journal_id": "jegrowth", "issue_id": "jegrowth-31-3"}
            created = seed_only_if_absent(root, candidate)
            self.assertEqual(2, len(created))
            self.assertTrue(all(
                p.startswith("public/api/v1/journals/jegrowth/issues/")
                for p in created
            ))
            self.assertEqual([], seed_only_if_absent(root, candidate))
            with self.assertRaisesRegex(ValueError, "existing production payload differs"):
                seed_only_if_absent(root, {"journal_id": "other"})

    def test_preexisting_partial_mismatch_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "public/api/v1/journals/jegrowth/issues/current.json"
            current.parent.mkdir(parents=True)
            current.write_text('{"source":"unrelated"}\\n')
            with self.assertRaisesRegex(ValueError, "existing production payload differs"):
                seed_only_if_absent(root, {"journal_id": "jegrowth"})
            self.assertFalse((current.parent / "jegrowth-31-3.json").exists())


if __name__ == "__main__":
    unittest.main()
