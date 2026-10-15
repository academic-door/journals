"""ENERGY 163 publisher issue delta: source-grounded 37+1 observation.

This test does not fetch ScienceDirect or write production data. It validates
the user-authorized browser observation and preserves the prior provenance
as an independent 33+1 historical reference, rather than treating READY as
a current publisher-completeness attestation.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse

from scripts.import_official_roster_evidence import validate_evidence

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data/provenance/official-rosters/sciencedirect/energy-163-c.json"
CURRENT = ROOT / "tests/fixtures/energy_163_official_browser_2026_10_15.json"
NEW = {
    "10.1016/j.eneco.2026.109642": "S0140988326005220",
    "10.1016/j.eneco.2026.109643": "S0140988326005232",
    "10.1016/j.eneco.2026.109640": "S0140988326005207",
    "10.1016/j.eneco.2026.109646": "S0140988326005268",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Energy163OfficialDeltaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prior = load(OLD)
        self.current = load(CURRENT)

    def test_exact_first_party_37_research_plus_one_erratum(self) -> None:
        validate_evidence(self.current)
        self.assertEqual(("energy", "energy-163-c"),
                         (self.current["journal_id"], self.current["issue_id"]))
        self.assertEqual("in-progress", self.current["publisher_issue_status"])
        self.assertEqual("browser-authorized", self.current["method"])
        self.assertNotEqual(self.prior["captured_at"], self.current["captured_at"])
        self.assertEqual(33, len(self.prior["items"]))
        self.assertEqual(37, len(self.current["items"]))
        self.assertEqual(1, len(self.current["excluded_items"]))
        self.assertEqual(1, self.current["excluded_item_count"])
        self.assertEqual(
            self.prior["excluded_items"][0]["doi"],
            self.current["excluded_items"][0]["doi"],
        )

    def test_exact_four_new_doi_pii_and_complete_restoration_metadata(self) -> None:
        prior_by_doi = {x["doi"]: x for x in self.prior["items"]}
        current_by_doi = {x["doi"]: x for x in self.current["items"]}
        self.assertEqual(set(NEW), set(current_by_doi) - set(prior_by_doi))
        self.assertEqual(set(prior_by_doi), set(prior_by_doi) & set(current_by_doi))
        for doi, pii in NEW.items():
            row = current_by_doi[doi]
            self.assertEqual("pii:" + pii, row["source_id"])
            self.assertEqual(
                "https://www.sciencedirect.com/science/article/pii/" + pii,
                row["official_article_url"],
            )
            self.assertEqual(row["official_article_url"], row["source_url"])
            self.assertTrue(len(row["abstract_en"]) >= 500)
            self.assertGreater(len(row["authors"]), 0)
            self.assertEqual("research article", row["publisher_article_type"])
            self.assertEqual("https", urlparse(row["doi_source_url"]).scheme)
            self.assertTrue(row["doi_source_url"].endswith("/" + doi))
            self.assertEqual("publisher-registered-doi-resolution", row["doi_source"])

    def test_previous_33_research_metadata_preserved_exactly(self) -> None:
        # The import contract uses DOI identity and preserves existing
        # canonical translations. The evidence must not overwrite metadata
        # for previously archived research items.
        current_by_doi = {x["doi"]: x for x in self.current["items"]}
        for prior in self.prior["items"]:
            updated = current_by_doi[prior["doi"]]
            for field, value in prior.items():
                if field in {"sequence", "source_sequence"}:
                    continue
                self.assertEqual(value, updated[field], (prior["doi"], field))
        ordered_old = [
            x["doi"] for x in self.current["items"]
            if x["doi"] not in NEW
        ]
        self.assertEqual(
            [x["doi"] for x in self.prior["items"]], ordered_old
        )
        source_positions = [row["source_sequence"] for row in self.current["items"]]
        self.assertEqual(sorted(source_positions), source_positions)

    def test_misclassified_or_duplicate_publisher_record_fails_closed(self) -> None:
        invalid = copy.deepcopy(self.current)
        invalid["items"][24]["doi"] = invalid["items"][0]["doi"]
        with self.assertRaises(ValueError):
            validate_evidence(invalid)
        missing_abstract = copy.deepcopy(self.current)
        missing_abstract["items"][24]["abstract_en"] = ""
        with self.assertRaises(ValueError):
            validate_evidence(missing_abstract)


if __name__ == "__main__":
    unittest.main()
