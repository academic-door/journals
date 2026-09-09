from pathlib import Path
import unittest

import yaml

from collectors.history import parse_archive

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "field-history.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "backfill-field-history.yml"


class R2AEAFamilyContractTests(unittest.TestCase):
    def test_r2_aea_journals_use_live_aea_archives(self):
        journals = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
        expected = {
            "AEJAPP": "https://www.aeaweb.org/journals/app/issues",
            "AEJMACRO": "https://www.aeaweb.org/journals/mac/issues",
            "AEJMICRO": "https://www.aeaweb.org/journals/mic/issues",
            "AEJPOL": "https://www.aeaweb.org/journals/pol/issues",
            "JEP": "https://www.aeaweb.org/journals/jep/issues",
            "AERI": "https://www.aeaweb.org/journals/aeri/issues",
        }
        for key, archive_url in expected.items():
            with self.subTest(journal=key):
                definition = journals[key]
                self.assertEqual("aea", definition["platform"])
                self.assertEqual(archive_url, definition["archive_url"])
                self.assertEqual("www.aeaweb.org", definition["allowed_host"])
                self.assertNotIn("observed_evidence_path", definition)

    def test_scheduled_refresh_includes_jep_and_aeri(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "--journals AER,AEJAPP,AEJMACRO,AEJMICRO,AEJPOL,JEP,AERI",
            text,
        )

    def test_aea_archive_parser_accepts_jep_and_aeri_issue_links(self):
        cases = (
            (
                "JEP",
                "https://www.aeaweb.org/journals/jep/issues",
                b'<a href="/issues/777">Vol. 40, No. 3, Summer 2026</a>',
                "jep-40-3",
            ),
            (
                "AERI",
                "https://www.aeaweb.org/journals/aeri/issues",
                b'<a href="/issues/778">Vol. 8, No. 3, September 2026</a>',
                "aeri-8-3",
            ),
        )
        for journal, archive_url, html, expected_id in cases:
            with self.subTest(journal=journal):
                issues = parse_archive(
                    html,
                    archive_url,
                    journal=journal,
                    platform="aea",
                    years=[2026],
                    allowed_host="www.aeaweb.org",
                )
                self.assertEqual([expected_id], [item.issue_id for item in issues])

    def test_jpe_qe_observed_snapshot_contract_is_unchanged(self):
        journals = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
        for key in ("JPE", "QE"):
            self.assertEqual("year_ranges", journals[key]["platform"])
            self.assertIn("observed_evidence_path", journals[key])


if __name__ == "__main__":
    unittest.main()
