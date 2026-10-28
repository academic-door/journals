"""Offline negative/positive tests for JAE 41(6) Crossref metadata-only audit."""
import copy
import json
from pathlib import Path
import unittest

from collectors.jae_crossref_audit import (
    EXPECTED_DOIS, audit_deposit, audit_issue, assert_official_roster,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures/b8/wiley-jae-41-6-source-stage.json"


def make_deposit(row):
    names = row["authors_en"]
    return {
        "DOI": row["doi"],
        "publisher": "Wiley",
        "container-title": ["Journal of Applied Econometrics"],
        "volume": "41",
        "issue": "6",
        "title": [row["title_en"]],
        "author": [
            {"given": name.rsplit(" ", 1)[0], "family": name.rsplit(" ", 1)[1]}
            for name in names
        ],
        "abstract": "<jats:p>Abstract test content with a publication method, "
                    "a quantitative implication, and sufficient text for the "
                    "nonempty validation threshold. No real article body.</jats:p>",
        "license": [{"URL": "http://onlinelibrary.wiley.com/termsAndConditions#vor"}],
    }


class JAECrossrefAuditTests(unittest.TestCase):
    def setUp(self):
        self.official = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.rows = assert_official_roster(self.official)
        self.deposits = [make_deposit(row) for row in self.rows]

    def test_strict_publisher_order_and_types(self):
        self.assertEqual(tuple(r["doi"] for r in self.rows), EXPECTED_DOIS)
        out = audit_issue(self.official, self.deposits)
        self.assertEqual(12, len(out["articles"]))
        self.assertFalse(out["enable"])
        self.assertEqual("BLOCKED_PENDING_PER_ARTICLE_REVIEW", out["rights_and_translation_gate"])
        self.assertTrue(all(not row["abstract_reuse_rights_verified"]
                            and not row["production_en_ingested"]
                            and not row["production_cn_translation_verified"]
                            and not row["eligible_for_activation"]
                            and len(row["abstract_sha256"]) == 64
                            and "abstract" not in row
                            for row in out["articles"]))

    def test_fail_closed_against_wrong_identity_and_missing_abstract(self):
        variations = [
            ("DOI", "10.1002/jae.99999"),
            ("publisher", "Other"),
            ("volume", "40"),
            ("issue", "5"),
            ("container-title", ["Another Journal"]),
            ("title", ["Unrelated title"]),
            ("author", []),
            ("abstract", ""),
        ]
        for key, value in variations:
            with self.subTest(key=key):
                wrong = copy.deepcopy(self.deposits[0])
                wrong[key] = value
                with self.assertRaises(ValueError):
                    audit_deposit(self.rows[0], wrong)

    def test_hyphen_typography_only(self):
        row = copy.deepcopy(self.rows[8])
        item = make_deposit(row)
        item["title"] = [row["title_en"].replace("Likelihood-Free", "Likelihood‐Free")]
        self.assertTrue(audit_deposit(row, item)["title_author_volume_issue_match"])

    def test_rejects_incomplete_or_reordered_roster(self):
        with self.assertRaisesRegex(ValueError, "incomplete_deposit_batch"):
            audit_issue(self.official, self.deposits[:-1])
        wrong = copy.deepcopy(self.official)
        wrong["observed_articles"][0], wrong["observed_articles"][1] = (
            wrong["observed_articles"][1], wrong["observed_articles"][0]
        )
        with self.assertRaisesRegex(ValueError, "official_roster_mismatch"):
            audit_issue(wrong, self.deposits)
        wrong = copy.deepcopy(self.official)
        wrong["enabled"] = True
        with self.assertRaises(ValueError):
            audit_issue(wrong, self.deposits)

    def test_swapped_deposits_fail_closed(self):
        wrong = self.deposits[:]
        wrong[0], wrong[1] = wrong[1], wrong[0]
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            audit_issue(self.official, wrong)


if __name__ == "__main__":
    unittest.main()
