"""Rights-qualified B8 public projection is whitelist-only and opt-in."""
import copy
import json
import unittest
from pathlib import Path

from scripts.b8_rights_qualified import CC_BY_4, MODE, project_b8_issue

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "public/api/v1/journals/jegrowth/issues/current.json"


class B8RightsQualifiedProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Synthetic JAE-shaped test only; never a source/production claim.
        cls.original = json.loads(SOURCE.read_text(encoding="utf-8"))
        cls.original["journal_id"] = "jae"
        cls.original["issue_id"] = "jae-41-6"
        cls.original["volume"] = "41"
        cls.original["issue"] = "6"
        cls.original["journal_name"] = "Journal of Applied Econometrics"
        cls.original["source_url"] = "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6"
        for row, suffix in zip(cls.original["articles"], ("70067", "70061", "70071"), strict=True):
            row["doi"] = "10.1002/jae." + suffix
            row["source_url"] = "https://onlinelibrary.wiley.com/doi/10.1002/jae." + suffix
            row["sources"] = {"issue": cls.original["source_url"], "roster": cls.original["source_url"]}

    def project(self, candidate=None, decisions=None):
        return project_b8_issue(
            candidate if candidate is not None else self.original,
            expected_journal_id="jae",
            expected_issue_id="jae-41-6",
            rights_by_doi=decisions,
        )

    def test_unapproved_abstracts_and_chinese_never_escape(self):
        private = copy.deepcopy(self.original)
        private["quality"]["sensitive_private_extract"] = "PRIVATE_NEVER_PUBLISH"
        private["secret_extra"] = "PRIVATE_TOP_LEVEL"
        for row in private["articles"]:
            row["sensitive_private_extract"] = "PRIVATE_ARTICLE"
            row["sources"]["dangerous_blob"] = "PRIVATE_SOURCE"
        original_bytes = json.dumps(private, ensure_ascii=False)
        projected = self.project(private)
        serialized = json.dumps(projected, ensure_ascii=False)
        self.assertEqual("ready", projected["publication_state"])
        self.assertEqual("metadata_complete", projected["content_status"])
        self.assertEqual(MODE, projected["public_mode"])
        self.assertEqual(len(private["articles"]), projected["public_content"]["metadata_articles"])
        self.assertEqual(0, projected["public_content"]["abstracts_en_visible"])
        self.assertEqual(0, projected["public_content"]["translations_cn_visible"])
        self.assertEqual(len(private["articles"]), projected["public_content"]["rights_withheld_articles"])
        self.assertEqual(
            [x["doi"] for x in private["articles"]],
            [x["doi"] for x in projected["articles"]],
        )
        for a, b in zip(private["articles"], projected["articles"]):
            self.assertEqual(a["title_en"], b["title_en"])
            self.assertEqual("", b["title_cn"])
            self.assertEqual("", b["abstract_en"])
            self.assertEqual("", b["abstract_cn"])
            self.assertEqual({"status": "rights_withheld"}, b["translation"])
            self.assertEqual("withheld", b["public_rights"]["status"])
            self.assertNotIn("sensitive_private_extract", b)
        self.assertNotIn("PRIVATE_NEVER_PUBLISH", serialized)
        self.assertNotIn("PRIVATE_TOP_LEVEL", serialized)
        self.assertNotIn("PRIVATE_ARTICLE", serialized)
        self.assertNotIn("PRIVATE_SOURCE", serialized)
        self.assertEqual(original_bytes, json.dumps(private, ensure_ascii=False))

    def test_excluded_item_preserves_only_safe_doi_type_metadata(self):
        source = copy.deepcopy(self.original)
        source["quality"]["excluded_items"] = [{
            "doi": "10.1002/jae.excluded",
            "title_en": "Correction notice",
            "article_type": "correction",
            "reason": "correction_notice",
            "abstract_en": "PRIVATE_CORRECTION_ABSTRACT",
            "source_html": "PRIVATE_SOURCE_HTML",
        }]
        public = self.project(source)
        row = public["quality"]["excluded_items"][0]
        self.assertEqual("correction", row["article_type"])
        self.assertEqual("10.1002/jae.excluded", row["doi"])
        self.assertNotIn("abstract_en", row)
        self.assertNotIn("PRIVATE_CORRECTION_ABSTRACT", json.dumps(public))
        self.assertNotIn("PRIVATE_SOURCE_HTML", json.dumps(public))

    def test_one_explicit_verified_cc_by_allows_only_one_bilingual_record(self):
        src = self.original
        doi = src["articles"][0]["doi"]
        grant = {
            "status": "verified_cc_by_4_0",
            "license_url": CC_BY_4,
            "evidence_url": src["articles"][0]["source_url"],
            "reviewed_by": "source-license-review",
            "attribution": "Named authors; CC BY 4.0; translated/adapted by Academic Door",
        }
        projected = self.project(decisions={doi.casefold(): grant})
        self.assertEqual(1, projected["public_content"]["abstracts_en_visible"])
        self.assertEqual(1, projected["public_content"]["translations_cn_visible"])
        self.assertEqual(src["articles"][0]["abstract_en"], projected["articles"][0]["abstract_en"])
        self.assertEqual(src["articles"][0]["abstract_cn"], projected["articles"][0]["abstract_cn"])
        self.assertEqual("verified_cc_by_4_0", projected["articles"][0]["public_rights"]["status"])
        self.assertEqual("", projected["articles"][1]["abstract_en"])

    def test_reject_wrong_license_unreviewed_or_no_derivatives(self):
        doi = self.original["articles"][0]["doi"].casefold()
        row = self.original["articles"][0]
        grant = {
            "status": "verified_cc_by_4_0", "license_url": CC_BY_4,
            "evidence_url": row["source_url"],
            "reviewed_by": "source-license-review", "attribution": "authors",
        }
        for bad in [
            {**grant, "license_url": "https://creativecommons.org/licenses/by-nc/4.0/"},
            {**grant, "license_url": "https://creativecommons.org/licenses/by-nc-nd/4.0/"},
            {**grant, "reviewed_by": ""},
            {**grant, "attribution": ""},
            {**grant, "evidence_url": ""},
            {**grant, "status": "publisher_standard"},
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.project(decisions={doi: bad})

    def test_reject_source_and_doi_scope_drift(self):
        source = copy.deepcopy(self.original)
        source["source_status"] = "source_pending"
        with self.assertRaises(ValueError):
            self.project(source)
        source = copy.deepcopy(self.original)
        source["articles"][1]["doi"] = source["articles"][0]["doi"]
        with self.assertRaisesRegex(ValueError, "unique"):
            self.project(source)
        source = copy.deepcopy(self.original)
        source["articles"][0]["sequence"] = 3
        with self.assertRaisesRegex(ValueError, "contiguous"):
            self.project(source)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.project(decisions={"10.0000/unrelated": {"status": "withheld"}})

    def test_reject_bad_source_url_or_article_types(self):
        bad = copy.deepcopy(self.original)
        bad["articles"][0]["source_url"] = "http://publisher.example/article"
        with self.assertRaises(ValueError):
            self.project(bad)
        bad = copy.deepcopy(self.original)
        bad["articles"][0]["article_type"] = "correction"
        with self.assertRaises(ValueError):
            self.project(bad)


if __name__ == "__main__":
    unittest.main()
