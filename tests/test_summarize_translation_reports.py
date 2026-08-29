from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.summarize_translation_reports import summarize


class SummarizeTranslationReportsTests(unittest.TestCase):
    def test_sums_shards_and_deduplicates_issue_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, payload in {
                "one": {"publishable_issue_ids": ["a"], "failed_issue_ids": ["x"], "translation_model_calls": 2, "translation_cache_reuses": 8},
                "two": {"publishable_issue_ids": ["a", "b"], "failed_issue_ids": ["y"], "translation_model_calls": 3, "translation_cache_reuses": 9},
            }.items():
                path = root / name / "output"
                path.mkdir(parents=True)
                (path / "translation-fix.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(
                {
                    "publishable_issue_ids": ["a", "b"],
                    "failed_issue_ids": ["x", "y"],
                    "translation_model_calls": 5,
                    "translation_cache_reuses": 17,
                },
                summarize(root),
            )

    def test_writes_machine_readable_summary_for_shell_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "one" / "output"
            report_dir.mkdir(parents=True)
            (report_dir / "translation-fix.json").write_text(
                json.dumps(
                    {
                        "publishable_issue_ids": ["energy-120-c"],
                        "failed_issue_ids": ["energy-126-c"],
                        "translation_model_calls": 2,
                        "translation_cache_reuses": 60,
                    }
                ),
                encoding="utf-8",
            )
            output = root / "summary.json"
            payload = summarize(root)
            output.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["translation_cache_reuses"], 60)


if __name__ == "__main__":
    unittest.main()
