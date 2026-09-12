import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

import yaml

from collectors.history import discover_official_issues
from scripts.backfill_history import discovery_authority


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "field-history.yml"

EXPECTED = {
    "AJAE": {
        2025: ("107", ("1", "2", "3", "4", "5"), "14678276"),
        2026: ("108", ("1", "2", "3", "4"), "14678276"),
    },
    "ECTA": {
        2025: ("93", ("1", "2", "3", "4", "5", "6"), "14680262"),
        2026: ("94", ("1", "2", "3", "4"), "14680262"),
    },
    "IER": {
        2025: ("66", ("1", "2", "3", "4", "5"), "14682354"),
        2026: ("67", ("1", "2", "3"), "14682354"),
    },
    "TE": {
        2025: ("20", ("1", "2", "3", "4"), "15557561"),
        2026: ("21", ("1", "2", "3"), "15557561"),
    },
    "RAND": {
        2025: ("56", ("1", "2", "3", "4"), "17562171"),
        2026: ("57", ("1", "2"), "17562171"),
    },
    "JF": {
        2025: ("80", ("1", "2", "3", "4", "5", "6"), "15406261"),
        2026: ("81", ("1", "2", "3", "4"), "15406261"),
    },
}


class R2WileyObservedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["journals"]

    def test_wiley_family_uses_publisher_observed_snapshots(self) -> None:
        for journal, years in EXPECTED.items():
            with self.subTest(journal=journal):
                definition = self.config[journal]
                self.assertEqual("official_archive_snapshot", discovery_authority(definition))
                self.assertEqual("onlinelibrary.wiley.com", definition["allowed_host"])
                evidence_path = ROOT / definition["observed_evidence_path"]
                payload = json.loads(evidence_path.read_text(encoding="utf-8"))
                self.assertEqual(journal, payload["journal"])
                self.assertEqual("official_publisher_archive", payload["authority"])
                self.assertEqual(
                    "chatgpt_web_official_page_observation",
                    payload["transport"],
                )
                self.assertTrue(payload["observed_at"].endswith("+00:00"))
                self.assertEqual({2025, 2026}, {int(source["year"]) for source in payload["sources"]})

                observed = discover_official_issues(journal, definition, years=range(2025, 2027))
                expected_rows = []
                for year, (volume, issues, product_id) in years.items():
                    expected_rows.extend(
                        (
                            year,
                            volume,
                            issue,
                            f"https://onlinelibrary.wiley.com/toc/{product_id}/{year}/{volume}/{issue}",
                        )
                        for issue in issues
                    )
                self.assertEqual(
                    expected_rows,
                    [
                        (item.year, item.volume, item.issue, item.official_url)
                        for item in observed
                    ],
                )
                for item in observed:
                    self.assertEqual("onlinelibrary.wiley.com", urlparse(item.official_url).hostname)

    def test_configured_wiley_issue_url_templates_use_observed_product_ids(self) -> None:
        for journal, years in EXPECTED.items():
            with self.subTest(journal=journal):
                product_ids = {row[2] for row in years.values()}
                self.assertEqual(1, len(product_ids))
                product_id = next(iter(product_ids))
                self.assertIn(
                    f"/toc/{product_id}/",
                    self.config[journal]["issue_url_template"],
                )

    def test_observation_does_not_infer_unpublished_2026_issues_from_cadence(self) -> None:
        expected_2026_counts = {
            "AJAE": 4,
            "ECTA": 4,
            "IER": 3,
            "TE": 3,
            "RAND": 2,
            "JF": 4,
        }
        for journal, expected_count in expected_2026_counts.items():
            with self.subTest(journal=journal):
                observed = discover_official_issues(
                    journal,
                    self.config[journal],
                    years=[2026],
                )
                self.assertEqual(expected_count, len(observed))
                self.assertEqual(
                    [str(index) for index in range(1, expected_count + 1)],
                    [item.issue for item in observed],
                )


if __name__ == "__main__":
    unittest.main()
