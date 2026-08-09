from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.backfill_status import period_label, summarize


class BackfillStatusTests(unittest.TestCase):
    def test_period_label_derives_year_range_from_filename(self) -> None:
        self.assertEqual(
            "2023-2024",
            period_label(Path("data/backfill-state/field-2023-2024.json")),
        )
        self.assertEqual(
            "2025-2026",
            period_label(Path("data/backfill-state/field-2025-2026.json")),
        )

    def test_summarize_groups_statuses(self) -> None:
        counts = summarize(
            {
                "a": {"status": "complete"},
                "b": {"status": "translation_partial"},
                "c": {"status": "blocked"},
                "d": {},
            }
        )
        self.assertEqual(1, counts["complete"])
        self.assertEqual(1, counts["translation_partial"])
        self.assertEqual(1, counts["blocked"])
        self.assertEqual(1, counts["pending"])

    def test_multi_state_output_includes_periods_and_years(self) -> None:
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_2023 = root / "field-2023-2024.json"
            state_2025 = root / "field-2025-2026.json"
            state_2023.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "issues": {
                            "aer-1-1": {
                                "journal": "AER",
                                "year": 2023,
                                "volume": "1",
                                "issue": "1",
                                "status": "complete",
                            },
                            "jde-2-2": {
                                "journal": "JDE",
                                "year": 2024,
                                "volume": "2",
                                "issue": "2",
                                "status": "translation_partial",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            state_2025.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "issues": {
                            "aer-5-1": {
                                "journal": "AER",
                                "year": 2025,
                                "volume": "5",
                                "issue": "1",
                                "status": "complete",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            out_json = root / "backfill-status.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.backfill_status",
                    "--state",
                    str(state_2023),
                    "--state",
                    str(state_2025),
                    "--out-json",
                    str(out_json),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(3, payload["summary"]["complete"] + payload["summary"]["translation_partial"])
        self.assertEqual(1, payload["summary"]["translation_partial"])
        self.assertIn("2023-2024", payload["periods"])
        self.assertIn("2025-2026", payload["periods"])
        self.assertEqual(2, payload["periods"]["2023-2024"]["issue_count"])
        self.assertEqual(1, payload["periods"]["2025-2026"]["issue_count"])
        self.assertIn("2023", payload["years"])
        self.assertIn("2024", payload["years"])
        self.assertIn("2025", payload["years"])
        self.assertEqual(1, payload["years"]["2024"]["summary"]["translation_partial"])


if __name__ == "__main__":
    unittest.main()
