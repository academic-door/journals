from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.backfill_history import COLLECTOR_REVISION
from scripts.backfill_status import build_payload, period_label, summarize


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
        # Legacy complete labels with no archive are no longer reported as
        # complete merely because a checkpoint exists.
        self.assertEqual(0, payload["summary"]["complete"])
        self.assertEqual(1, payload["summary"]["translation_partial"])
        self.assertEqual(2, payload["summary"]["blocked"])
        self.assertIn("2023-2024", payload["periods"])
        self.assertIn("2025-2026", payload["periods"])
        self.assertEqual(2, payload["periods"]["2023-2024"]["issue_count"])
        self.assertEqual(1, payload["periods"]["2025-2026"]["issue_count"])
        self.assertIn("2023", payload["years"])
        self.assertIn("2024", payload["years"])
        self.assertIn("2025", payload["years"])
        self.assertEqual(1, payload["years"]["2024"]["summary"]["translation_partial"])

    def test_schema_12_coverage_uses_discovery_and_archive_readback(self) -> None:
        article = {
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
            "articles": [article],
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_dir = root / "api" / "journals" / "aer" / "issues"
            archive_dir.mkdir(parents=True)
            (archive_dir / "aer-114-1.json").write_text(
                json.dumps(issue), encoding="utf-8"
            )
            state_path = root / "field-2023-2024.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "issues": {
                            "aer-114-1": {
                                "journal": "AER",
                                "year": 2024,
                                "volume": "114",
                                "issue": "1",
                                "status": "ready",
                            }
                        },
                        "discovery": {
                            "AER": {
                                "issue_ids": ["aer-114-1", "aer-114-2"],
                                "issue_years": {
                                    "aer-114-1": 2024,
                                    "aer-114-2": 2024,
                                },
                                "authority": "official_archive",
                                "refreshed_at": "2026-08-11T00:00:00+00:00",
                                "collector_revision": COLLECTOR_REVISION,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            payload = build_payload(
                [state_path],
                journals={"AER": {"id": "aer", "name": "AER"}},
                api_root=root / "api",
            )
        self.assertEqual("1.2", payload["schema_version"])
        self.assertEqual(2, payload["coverage"]["discovered"])
        self.assertEqual(1, payload["coverage"]["archived"])
        self.assertEqual(1, payload["coverage"]["publication_ready"])
        self.assertEqual(1, payload["coverage"]["missing"])
        self.assertEqual(["aer-114-2"], payload["coverage"]["missing_issue_ids"])
        self.assertEqual(
            1, payload["journal_coverage"]["AER"]["years"]["2024"]["missing"]
        )


if __name__ == "__main__":
    unittest.main()
