from __future__ import annotations

import unittest

from scripts.build_recovery_queue import build_queue


class RecoveryQueueTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
