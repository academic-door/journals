from __future__ import annotations

import unittest
from pathlib import Path

from scripts.build_recovery_queue import build_forecast, build_queue


class RecoveryQueueTests(unittest.TestCase):
    def test_forecast_counts_known_translation_work_without_calling_model(self) -> None:
        forecast = build_forecast(
            [
                {
                    "action": "translation",
                    "records": [
                        {
                            "archive_exists": True,
                            "counts": {"articles": 11, "translation_cn": 8},
                        }
                    ],
                },
                {
                    "action": "browser",
                    "records": [
                        {
                            "archive_exists": False,
                            "counts": {"articles": 0, "translation_cn": 0},
                        }
                    ],
                },
            ]
        )
        self.assertEqual(2, forecast["issue_count"])
        self.assertEqual(11, forecast["article_count_known"])
        self.assertEqual(3, forecast["translation_calls_estimate"])
        self.assertEqual(2, forecast["new_ready_upper_bound"])

    def test_chunks_exact_issue_ids_by_action_without_ready_records(self) -> None:
        manifest = {
            "generated_at": "2026-08-26T00:00:00+00:00",
            "records": [
                {
                    "issue_id": f"eer-{number}-c",
                    "journal": "EER",
                    "year": 2024,
                    "category": "recoverable",
                }
                for number in range(1, 14)
            ]
            + [
                {
                    "issue_id": "aer-114-1",
                    "journal": "AER",
                    "year": 2024,
                    "category": "ready",
                }
            ],
        }
        matrix, shards = build_queue(
            manifest,
            {
                "EER": {"collector": "elsevier"},
                "AER": {"collector": "aea"},
            },
            categories={"recoverable"},
            chunk_size=10,
        )
        self.assertEqual(2, len(matrix["include"]))
        self.assertEqual([10, 3], [len(item["issue_ids"]) for item in shards])
        self.assertNotIn("aer-114-1", matrix["include"][0]["issue_ids"])
        self.assertTrue(all(item["action"] == "collect-elsevier" for item in shards))

    def test_routes_source_evidence_to_publisher_adapter(self) -> None:
        manifest = {
            "records": [
                {
                    "issue_id": "aer-114-2",
                    "journal": "AER",
                    "year": 2024,
                    "category": "source_pending",
                },
                {
                    "issue_id": "eer-188-c",
                    "journal": "EER",
                    "year": 2026,
                    "category": "source_pending",
                },
            ]
        }
        _, shards = build_queue(
            manifest,
            {"AER": {"collector": "aea"}, "EER": {"collector": "elsevier"}},
            categories={"source_pending"},
            chunk_size=10,
        )
        self.assertEqual({"aea-evidence", "browser"}, {item["action"] for item in shards})

    def test_recollects_exact_oup_source_pending_issue_from_official_page(self) -> None:
        _, shards = build_queue(
            {
                "records": [
                    {
                        "issue_id": "ej-134-1",
                        "journal": "EJ",
                        "year": 2024,
                        "category": "source_pending",
                    }
                ]
            },
            {"EJ": {"collector": "oup"}},
            categories={"source_pending"},
            chunk_size=10,
        )
        self.assertEqual("collect-oup", shards[0]["action"])

    def test_routes_springer_recoverable_issue_to_official_evidence(self) -> None:
        _, shards = build_queue(
            {
                "records": [
                    {
                        "issue_id": "ere-84-1",
                        "journal": "ERE",
                        "year": 2023,
                        "category": "recoverable",
                        "source_status": "source_pending",
                        "official_url": "https://link.springer.com/journal/10640/volumes-and-issues",
                    }
                ]
            },
            {"ERE": {"collector": "repec"}},
            categories={"recoverable"},
            chunk_size=10,
        )
        self.assertEqual("springer-evidence", shards[0]["action"])

    def test_filters_to_explicit_issue_ids(self) -> None:
        manifest = {
            "records": [
                {
                    "issue_id": "jedc-147-c",
                    "journal": "JEDC",
                    "year": 2023,
                    "category": "translation_required",
                },
                {
                    "issue_id": "jde-161-c",
                    "journal": "JDE",
                    "year": 2023,
                    "category": "translation_required",
                },
            ]
        }
        matrix, shards = build_queue(
            manifest,
            {"JEDC": {"collector": "elsevier"}, "JDE": {"collector": "elsevier"}},
            categories={"translation_required"},
            chunk_size=10,
            issue_ids={"jedc-147-c"},
        )
        self.assertEqual(["jedc-147-c"], shards[0]["issue_ids"])
        self.assertEqual("jedc-147-c", matrix["include"][0]["issue_ids"])

    def test_cli_issue_count_is_number_of_issues_not_string_length(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "scripts" / "build_recovery_queue.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "sum(len(item['issue_ids'].split(',')) for item in matrix['include'])",
            source,
        )


if __name__ == "__main__":
    unittest.main()
