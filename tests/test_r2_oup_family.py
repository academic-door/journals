from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from collectors.history import parse_archive
from scripts.backfill_history import discovery_authority

ROOT = Path(__file__).resolve().parents[1]


class R2OUPFamilyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load(
            (ROOT / "config/field-history.yml").read_text(encoding="utf-8")
        )["journals"]

    def test_qje_and_res_use_live_oup_year_archives(self) -> None:
        expected = {
            "QJE": "https://academic.oup.com/qje/issue-archive/{year}",
            "RES": "https://academic.oup.com/restud/issue-archive/{year}",
        }
        for key, archive_template in expected.items():
            with self.subTest(journal=key):
                definition = self.config[key]
                self.assertEqual("oup", definition["platform"])
                self.assertEqual(archive_template, definition["archive_url_template"])
                self.assertEqual("academic.oup.com", definition["allowed_host"])
                self.assertEqual("official_archive", discovery_authority(definition))

    def test_other_oup_journals_remain_crossref_candidates(self) -> None:
        for key in ("EJ", "JEEA", "RFS"):
            with self.subTest(journal=key):
                definition = self.config[key]
                self.assertEqual("crossref", definition["platform"])
                self.assertEqual("crossref_candidate", discovery_authority(definition))
                self.assertNotIn("archive_url_template", definition)

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
                b'<a href="/restud/issue/93/4">Volume 93, Issue 4, October 2026</a>',
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
