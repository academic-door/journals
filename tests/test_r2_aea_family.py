from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "field-history.yml"


class R2AEAFamilyContractTests(unittest.TestCase):
    def test_four_aej_journals_use_live_aea_archives(self):
        journals = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
        expected = {
            "AEJAPP": "https://www.aeaweb.org/journals/app/issues",
            "AEJMACRO": "https://www.aeaweb.org/journals/mac/issues",
            "AEJMICRO": "https://www.aeaweb.org/journals/mic/issues",
            "AEJPOL": "https://www.aeaweb.org/journals/pol/issues",
        }
        for key, archive_url in expected.items():
            with self.subTest(journal=key):
                definition = journals[key]
                self.assertEqual("aea", definition["platform"])
                self.assertEqual(archive_url, definition["archive_url"])
                self.assertEqual("www.aeaweb.org", definition["allowed_host"])
                self.assertNotIn("observed_evidence_path", definition)

    def test_jpe_qe_observed_snapshot_contract_is_unchanged(self):
        journals = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
        for key in ("JPE", "QE"):
            self.assertEqual("year_ranges", journals[key]["platform"])
            self.assertIn("observed_evidence_path", journals[key])


if __name__ == "__main__":
    unittest.main()
