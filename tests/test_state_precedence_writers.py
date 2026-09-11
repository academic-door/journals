from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.merge_history_shards import merge_state
from scripts.publish_data_delta import _merge_backfill_state_json


ROOT = Path(__file__).resolve().parents[1]


class StatePrecedenceWriterTests(unittest.TestCase):
    @staticmethod
    def _snapshot(authority: str, refreshed_at: str, url: str) -> dict:
        return {
            "issue_ids": ["ere-85-3-4"],
            "issue_refs": {
                "ere-85-3-4": {
                    "journal": "ERE",
                    "year": 2023,
                    "volume": "85",
                    "issue": "3-4",
                    "official_url": url,
                }
            },
            "authority": authority,
            "refreshed_at": refreshed_at,
            "collector_revision": "history-integrity-2026-08-11",
        }

    def test_shard_merge_never_replaces_authoritative_discovery_with_newer_candidate(self) -> None:
        exact = "https://link.springer.com/journal/10640/volumes-and-issues/85-3"
        root = "https://link.springer.com/journal/10640/volumes-and-issues"
        base = {
            "issues": {},
            "discovery": {
                "ERE": self._snapshot(
                    "official_archive", "2026-09-10T18:11:14+00:00", exact
                )
            },
        }
        shard = {
            "issues": {},
            "discovery": {
                "ERE": self._snapshot(
                    "crossref_candidate", "2026-09-10T20:00:00+00:00", root
                )
            },
        }
        merged = merge_state(base, shard, {"ERE"})
        self.assertEqual("official_archive", merged["discovery"]["ERE"]["authority"])
        self.assertEqual(
            exact,
            merged["discovery"]["ERE"]["issue_refs"]["ere-85-3-4"]["official_url"],
        )

    def test_delta_merge_never_replaces_authoritative_discovery_with_newer_candidate(self) -> None:
        exact = "https://link.springer.com/journal/10640/volumes-and-issues/85-3"
        root = "https://link.springer.com/journal/10640/volumes-and-issues"
        old = self._snapshot(
            "crossref_candidate", "2026-08-24T17:03:09+00:00", root
        )
        authoritative = self._snapshot(
            "official_archive", "2026-09-10T18:11:14+00:00", exact
        )
        newer_candidate = self._snapshot(
            "crossref_candidate", "2026-09-10T20:00:00+00:00", root
        )
        with tempfile.TemporaryDirectory() as temporary:
            root_dir = Path(temporary)
            baseline = root_dir / "baseline.json"
            generated = root_dir / "generated.json"
            target = root_dir / "target.json"
            for path, snapshot in (
                (baseline, old),
                (generated, authoritative),
                (target, newer_candidate),
            ):
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": "1.1",
                            "issues": {},
                            "discovery": {"ERE": snapshot},
                            "rotation": {},
                        }
                    ),
                    encoding="utf-8",
                )
            self.assertTrue(_merge_backfill_state_json(baseline, generated, target))
            merged = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual("official_archive", merged["discovery"]["ERE"]["authority"])
        self.assertEqual(
            exact,
            merged["discovery"]["ERE"]["issue_refs"]["ere-85-3-4"]["official_url"],
        )

    def test_writer_scripts_support_direct_cli_execution_without_pythonpath(self) -> None:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        for relative_path in (
            "scripts/merge_history_shards.py",
            "scripts/publish_data_delta.py",
        ):
            with self.subTest(script=relative_path):
                result = subprocess.run(
                    [sys.executable, relative_path, "--help"],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    0,
                    result.returncode,
                    msg=f"{relative_path} failed direct CLI execution:\n{result.stderr}",
                )


if __name__ == "__main__":
    unittest.main()
