from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from collectors.history import discover_official_issues, parse_archive
from scripts.backfill_history import discovery_authority, discovery_refreshed_at

ROOT = Path(__file__).resolve().parents[1]


class R2OUPFamilyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load(
            (ROOT / "config/field-history.yml").read_text(encoding="utf-8")
        )["journals"]

    def test_tracked_oup_journals_use_observed_archive_evidence(self) -> None:
        expected = {
            "QJE": (
                "https://academic.oup.com/qje/issue-archive/{year}",
                "data/provenance/expected-set-observations/qje-2025-2026.json",
                "2026-09-08T11:13:10+00:00",
            ),
            "RES": (
                "https://academic.oup.com/restud/issue-archive/{year}",
                "data/provenance/expected-set-observations/res-2025-2026.json",
                "2026-09-09T09:35:01+00:00",
            ),
            "EJ": (
                "https://academic.oup.com/ej/issue-archive/{year}",
                "data/provenance/expected-set-observations/ej-2025-2026.json",
                "2026-09-08T15:23:03+00:00",
            ),
            "JEEA": (
                "https://academic.oup.com/jeea/issue-archive/{year}",
                "data/provenance/expected-set-observations/jeea-2025-2026.json",
                "2026-09-08T15:23:03+00:00",
            ),
            "RFS": (
                "https://academic.oup.com/rfs/issue-archive/{year}",
                "data/provenance/expected-set-observations/rfs-2025-2026.json",
                "2026-09-08T15:23:03+00:00",
            ),
        }
        for key, (archive_template, evidence_path, refreshed_at) in expected.items():
            with self.subTest(journal=key):
                definition = self.config[key]
                self.assertEqual("oup", definition["platform"])
                self.assertEqual(archive_template, definition["archive_url_template"])
                self.assertEqual(evidence_path, definition["observed_evidence_path"])
                self.assertEqual("academic.oup.com", definition["allowed_host"])
                self.assertEqual(
                    "official_archive_snapshot", discovery_authority(definition)
                )
                self.assertEqual(
                    refreshed_at,
                    discovery_refreshed_at(definition),
                )

    def test_observed_oup_snapshots_have_exact_2025_2026_issue_sets(self) -> None:
        expected = {
            "QJE": [
                "qje-140-1", "qje-140-2", "qje-140-3", "qje-140-4",
                "qje-141-1", "qje-141-2", "qje-141-3",
            ],
            "RES": [
                "res-92-1", "res-92-2", "res-92-3", "res-92-4", "res-92-5", "res-92-6",
                "res-93-1", "res-93-2", "res-93-3", "res-93-4", "res-93-5",
            ],
            "EJ": [
                *[f"ej-135-{issue}" for issue in range(667, 673)],
                *[f"ej-136-{issue}" for issue in range(673, 679)],
            ],
            "JEEA": [
                *[f"jeea-23-{issue}" for issue in range(1, 7)],
                *[f"jeea-24-{issue}" for issue in range(1, 5)],
            ],
            "RFS": [
                *[f"rfs-38-{issue}" for issue in range(1, 13)],
                *[f"rfs-39-{issue}" for issue in range(1, 10)],
            ],
        }
        for key, expected_ids in expected.items():
            with self.subTest(journal=key):
                issues = discover_official_issues(
                    key,
                    self.config[key],
                    years=range(2025, 2027),
                )
                self.assertEqual(expected_ids, [item.issue_id for item in issues])
                self.assertTrue(
                    all(
                        item.official_url.startswith("https://academic.oup.com/")
                        for item in issues
                    )
                )

    def test_res_snapshot_records_cumulative_publisher_observation_chain(self) -> None:
        path = ROOT / "data/provenance/expected-set-observations/res-2025-2026.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("composite_official_publisher_observation", payload["transport"])
        chain = payload["observation_chain"]
        self.assertEqual(2, len(chain))
        self.assertEqual("2026-09-08T11:13:10+00:00", chain[0]["observed_at"])
        self.assertEqual("2026-09-09T09:35:01+00:00", chain[1]["observed_at"])
        self.assertEqual(
            {
                "year": 2026,
                "volume": "93",
                "issue": "5",
                "official_url": "https://academic.oup.com/restud/issue/93/5",
            },
            chain[1]["latest_issue"],
        )

    def test_live_oup_archive_parser_handles_all_tracked_slugs(self) -> None:
        fixtures = (
            (
                "QJE",
                b'<a href="/qje/issue/141/3">Volume 141, Issue 3, August 2026</a>',
                "https://academic.oup.com/qje/issue-archive/2026",
                "qje-141-3",
            ),
            (
                "RES",
                b'<a href="/restud/issue/93/5">Volume 93, Issue 5, October 2026</a>',
                "https://academic.oup.com/restud/issue-archive/2026",
                "res-93-5",
            ),
            (
                "EJ",
                b'<a href="/ej/issue/136/678">Volume 136, Issue 678, August 2026</a>',
                "https://academic.oup.com/ej/issue-archive/2026",
                "ej-136-678",
            ),
            (
                "JEEA",
                b'<a href="/jeea/issue/24/4">Volume 24, Issue 4, August 2026</a>',
                "https://academic.oup.com/jeea/issue-archive/2026",
                "jeea-24-4",
            ),
            (
                "RFS",
                b'<a href="/rfs/issue/39/9">Volume 39, Issue 9, September 2026</a>',
                "https://academic.oup.com/rfs/issue-archive/2026",
                "rfs-39-9",
            ),
        )
        for journal, content, archive_url, expected_issue_id in fixtures:
            with self.subTest(journal=journal):
                issues = parse_archive(
                    content,
                    archive_url,
                    journal=journal,
                    platform="oup",
                    years=[2026],
                    allowed_host="academic.oup.com",
                )
                self.assertEqual([expected_issue_id], [item.issue_id for item in issues])


if __name__ == "__main__":
    unittest.main()
