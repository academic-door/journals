from __future__ import annotations

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

    def test_qje_and_res_use_observed_oup_archive_evidence(self) -> None:
        expected = {
            "QJE": (
                "https://academic.oup.com/qje/issue-archive/{year}",
                "data/provenance/expected-set-observations/qje-2025-2026.json",
            ),
            "RES": (
                "https://academic.oup.com/restud/issue-archive/{year}",
                "data/provenance/expected-set-observations/res-2025-2026.json",
            ),
        }
        for key, (archive_template, evidence_path) in expected.items():
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
                    "2026-09-08T11:13:10+00:00",
                    discovery_refreshed_at(definition),
                )

    def test_observed_oup_snapshot_has_exact_2025_2026_issue_sets(self) -> None:
        expected = {
            "QJE": [
                "qje-140-1",
                "qje-140-2",
                "qje-140-3",
                "qje-140-4",
                "qje-141-1",
                "qje-141-2",
                "qje-141-3",
            ],
            "RES": [
                "res-92-1",
                "res-92-2",
                "res-92-3",
                "res-92-4",
                "res-92-5",
                "res-92-6",
                "res-93-1",
                "res-93-2",
                "res-93-3",
                "res-93-4",
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

    def test_other_oup_journals_remain_crossref_candidates(self) -> None:
        for key in ("EJ", "JEEA", "RFS"):
            with self.subTest(journal=key):
                definition = self.config[key]
                self.assertEqual("crossref", definition["platform"])
                self.assertEqual("crossref_candidate", discovery_authority(definition))
                self.assertNotIn("archive_url_template", definition)
                self.assertNotIn("observed_evidence_path", definition)

    def test_live_oup_archive_parser_handles_qje_and_res(self) -> None:
        fixtures = (
            (
                "QJE",
                b'<a href="/qje/issue/141/3">Volume 141, Issue 3, August 2026</a>',
                "https://academic.oup.com/qje/issue-archive/2026",
                "qje-141-3",
            ),
            (
                "RES",
                b'<a href="/restud/issue/93/4">Volume 93, Issue 4, July 2026</a>',
                "https://academic.oup.com/restud/issue-archive/2026",
                "res-93-4",
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
