"""ENERGY publication-horizon gate anchored to a first-party in-progress issue.

Official ScienceDirect Vol 164 is labelled "In progress (December 2026)" on
2026-11-10. A November Crossref deposit must not replace validated 163(C).
The source/order/translation publication gates remain unchanged.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import unittest

import yaml

from scripts.journal_monitor import _lead_months, select_candidate


ROOT = Path(__file__).resolve().parents[1]


class ENERGYInProgressIssueHorizonTests(unittest.TestCase):
    def setUp(self) -> None:
        journals = yaml.safe_load(
            (ROOT / "config/journals.yml").read_text(encoding="utf-8")
        )["journals"]
        self.energy = journals["ENERGY"]
        self.labeco = journals["LABECO"]
        self.baseline = {
            "journal_id": "energy",
            "issue_id": "energy-163-c",
            "volume": "163",
            "issue": "C",
            "publication_date": "November 2026",
            "articles": [],
        }
        self.crossref_items = [{
            "DOI": "10.1016/j.eneco.2026.example",
            "title": ["Provisional future article"],
            "type": "journal-article",
            "volume": "164",
            "issue": "",
            "published": {"date-parts": [[2026, 12, 1]]},
        }]

    def test_energy_alone_does_not_predeclare_a_future_issue(self) -> None:
        self.assertEqual(self.energy["publication_lead_months"], 0)
        self.assertEqual(_lead_months(self.energy), 0)
        self.assertEqual(_lead_months(self.labeco), 1)
        pending = select_candidate(
            self.crossref_items, self.baseline,
            today=date(2026, 11, 10),
            publication_lead_months=_lead_months(self.energy),
        )
        self.assertIsNone(pending)

    def test_december_reenables_detection_without_declaring_ready(self) -> None:
        detected = select_candidate(
            self.crossref_items, self.baseline,
            today=date(2026, 12, 1),
            publication_lead_months=_lead_months(self.energy),
        )
        self.assertIsNotNone(detected)
        self.assertEqual(detected.volume, "164")
        self.assertEqual(detected.issue_key, "164:")
        self.assertEqual(detected.publication_date, "2026-12-01")


if __name__ == "__main__":
    unittest.main()
