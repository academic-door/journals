from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from scripts.build_recovery_queue import build_forecast, build_queue


class RecoveryQueueTests(unittest.TestCase):
    def test_direct_cli_execution_can_import_repo_modules(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "build_recovery_queue.py"), "--help"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

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

    def test_routes_authoritative_wiley_recoverable_issue_to_wiley_evidence(self) -> None:
        _, shards = build_queue(
            {
                "records": [
                    {
                        "issue_id": "te-21-2",
                        "journal": "TE",
                        "year": 2026,
                        "category": "recoverable",
                        "authority": "official_archive_snapshot",
                        "official_url": "https://onlinelibrary.wiley.com/toc/15557561/2026/21/2",
                    }
                ]
            },
            {"TE": {"collector": "repec"}},
            categories={"recoverable"},
            chunk_size=10,
        )
        self.assertEqual("wiley-evidence", shards[0]["action"])

    def test_candidate_wiley_route_does_not_gain_authoritative_evidence_adapter(self) -> None:
        _, shards = build_queue(
            {
                "records": [
                    {
                        "issue_id": "te-21-2",
                        "journal": "TE",
                        "year": 2026,
                        "category": "recoverable",
                        "authority": "crossref_candidate",
                        "official_url": "https://onlinelibrary.wiley.com/toc/15557561/2026/21/2",
                    }
                ]
            },
            {"TE": {"collector": "repec"}},
            categories={"recoverable"},
            chunk_size=10,
        )
        self.assertEqual("collect-repec", shards[0]["action"])

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


    def test_routes_configured_publisher_repec_before_wiley_or_browser(self) -> None:
        manifest = {
            "records": [
                {
                    "issue_id": "ajae-107-1",
                    "journal": "AJAE",
                    "year": 2025,
                    "category": "source_pending",
                    "official_url": "https://onlinelibrary.wiley.com/toc/14678276/2025/107/1",
                    "authority": "official_archive_snapshot",
                },
                {
                    "issue_id": "restat-107-1",
                    "journal": "RESTAT",
                    "year": 2025,
                    "category": "source_pending",
                    "official_url": "https://direct.mit.edu/rest/issue/107/1",
                },
                {
                    "issue_id": "red-57-c",
                    "journal": "RED",
                    "year": 2025,
                    "category": "source_pending",
                    "official_url": "https://www.sciencedirect.com/journal/review-of-economic-dynamics/vol/57/suppl/C",
                },
            ]
        }
        _, shards = build_queue(
            manifest,
            {
                "AJAE": {"collector": "wiley", "repec_series_code": "wly/ajagec"},
                "RESTAT": {"collector": "crossref", "repec_series_code": "tpr/restat"},
                "RED": {
                    "collector": "elsevier",
                    "repec_series_url": "https://ideas.repec.org/s/red/issued.html",
                },
            },
            categories={"source_pending"},
            chunk_size=10,
        )
        actions = {item["action"] for item in shards}
        self.assertEqual({"collect-repec", "collect-elsevier"}, actions)


if __name__ == "__main__":
    unittest.main()
