"""Guarded B8 seed only exports metadata; no publisher content leakage."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.seed_b8_rights_qualified import (
    SPECS, build_attested_projection, seed_only_if_absent,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "public/api/v1/journals/jegrowth/issues/current.json"


def private_source():
    item = json.loads(SOURCE.read_text(encoding="utf-8"))
    item["journal_id"] = "jae"
    item["journal_name"] = "Journal of Applied Econometrics"
    item["issue_id"] = "jae-41-6"
    item["volume"] = "41"
    item["issue"] = "6"
    item["source_url"] = "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6"
    for article, suffix in zip(item["articles"], ("70067", "70061", "70071"), strict=True):
        article["doi"] = "10.1002/jae." + suffix
        article["source_url"] = "https://onlinelibrary.wiley.com/doi/10.1002/jae." + suffix
        article["sources"] = {"issue": item["source_url"]}
    return item


class B8AttestedSeedTests(unittest.TestCase):
    def make_projection(self):
        with patch.dict(SPECS, {"JAE": (
            "jae", "jae-41-6", 3, "official_verified",
            12345, "test-artifact", "candidate.json", "report.json", 0,
        )}):
            return build_attested_projection("JAE", private_source(), {
                "content_quality_ready": True,
                "production_writes": False,
                "production_release_approved": False,
            })

    def test_private_candidate_never_reaches_public_data(self):
        projected = self.make_projection()
        self.assertEqual(3, len(projected["articles"]))
        self.assertEqual(3, projected["public_content"]["rights_withheld_articles"])
        self.assertEqual("metadata_complete", projected["content_status"])
        self.assertTrue(all(a["abstract_en"] == "" and a["abstract_cn"] == "" for a in projected["articles"]))
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            changed = seed_only_if_absent(folder, projected)
            self.assertEqual(2, len(changed))
            self.assertEqual([], seed_only_if_absent(folder, projected))
            readback = json.loads((folder/"public/api/v1/journals/jae/issues/current.json").read_text(encoding="utf-8"))
            self.assertEqual(projected, readback)
            self.assertNotIn(private_source()["articles"][0]["abstract_en"], json.dumps(readback))
            revised = copy.deepcopy(projected)
            revised["articles"][0]["title_en"] = "Unapproved revision"
            with self.assertRaisesRegex(ValueError, "existing public issue differs"):
                seed_only_if_absent(folder, revised)

    def test_rights_withheld_research_title_keeps_article_type_and_count(self):
        # The real JAE 41(6) title "Triply Robust Panel Estimators"
        # resembles a personal name to the legacy no-abstract heuristic.
        # Rights withholding is not evidence of an editorial/non-research item.
        from scripts.b8_rights_qualified import project_b8_issue, validate_public_projection
        from scripts.update_journals import normalize_issue_content

        candidate = private_source()
        candidate["articles"][0]["title_en"] = "Triply Robust Panel Estimators"
        candidate["articles"][0]["article_type"] = "research-article"
        public = project_b8_issue(
            candidate, expected_journal_id="jae",
            expected_issue_id="jae-41-6", rights_by_doi={},
        )
        normalized = normalize_issue_content(public)
        validate_public_projection(normalized)
        self.assertEqual(3, len(normalized["articles"]))
        self.assertEqual("metadata_complete", normalized["content_status"])
        self.assertEqual("ready", normalized["publication_state"])
        self.assertEqual("research-article", normalized["articles"][0]["article_type"])
        self.assertEqual("Triply Robust Panel Estimators", normalized["articles"][0]["title_en"])
        self.assertEqual(3, normalized["public_content"]["rights_withheld_articles"])
        self.assertEqual([], normalized["quality"].get("excluded_items", []))
        self.assertEqual(
            normalized,
            normalize_issue_content(copy.deepcopy(normalized)),
            "metadata projection must be deterministically normalized",
        )

    def test_joeg_rights_withheld_official_comments_keep_source_types(self):
        # Regression for a mixed OUP issue: issue-section-attested comments
        # remain publishable even if their titles match legacy editorial
        # heuristics after public text is deliberately withheld.
        candidate = private_source()
        candidate["journal_id"] = "joeg"
        candidate["issue_id"] = "joeg-26-5"
        candidate["journal_name"] = "Journal of Economic Geography"
        candidate["volume"] = "26"
        candidate["issue"] = "5"
        candidate["source_url"] = "https://academic.oup.com/joeg/issue/26/5"
        candidate["quality"]["roster_authority"] = "official-issue-page"
        candidate["quality"]["roster_transport"] = "publisher-full-toc-observed"
        types = ("comment", "comment", "research-article")
        titles = (
            "Introduction to regional economic geography",
            "Editorial report on the geography of firms",
            "Persistent patterns of regional economic growth",
        )
        for i, (article, article_type, title) in enumerate(
            zip(candidate["articles"], types, titles, strict=True), 1
        ):
            article["doi"] = "10.1093/jeg/lbag" + f"{i:03}"
            article["article_type"] = article_type
            article["title_en"] = title
            article["source_url"] = "https://academic.oup.com/joeg/article/26/5/" + str(i)
            article["sources"] = {"issue": candidate["source_url"]}
        with patch.dict(SPECS, {"JOEG": (
            "joeg", "joeg-26-5", 3, "official_verified",
            12345, "test-artifact", "candidate.json", "report.json", 0,
        )}):
            projected = build_attested_projection("JOEG", candidate, {
                "quality_ready": True,
                "production_writes": False,
                "release_enabled": False,
            })
        self.assertEqual(list(types), [a["article_type"] for a in projected["articles"]])
        self.assertEqual(3, len(projected["articles"]))
        self.assertEqual("ready", projected["publication_state"])
        self.assertEqual("official_verified", projected["source_status"])
        self.assertEqual([], projected["quality"].get("excluded_items", []))
        self.assertEqual(3, projected["public_content"]["rights_withheld_articles"])
        self.assertTrue(all(
            not a["abstract_en"] and not a["abstract_cn"] and not a["title_cn"]
            for a in projected["articles"]
        ))

    def test_source_report_must_not_claim_production(self):
        with patch.dict(SPECS, {"JAE": (
            "jae", "jae-41-6", 3, "official_verified",
            12345, "test-artifact", "candidate.json", "report.json", 0,
        )}):
            for report in [
                {"content_quality_ready": False, "production_writes": False, "production_release_approved": False},
                {"content_quality_ready": True, "production_writes": True, "production_release_approved": False},
                {"content_quality_ready": True, "production_writes": False, "production_release_approved": True},
            ]:
                with self.subTest(report=report), self.assertRaises(ValueError):
                    build_attested_projection("JAE", private_source(), report)

    def test_wrong_doi_order_and_issue_identity_fail_closed(self):
        with patch.dict(SPECS, {"JAE": (
            "jae", "jae-41-6", 3, "official_verified",
            12345, "test-artifact", "candidate.json", "report.json", 0,
        )}):
            report={"content_quality_ready": True, "production_writes": False, "production_release_approved": False}
            bad=private_source()
            bad["issue_id"]="jae-41-5"
            with self.assertRaises(ValueError):
                build_attested_projection("JAE", bad, report)
            bad=private_source()
            bad["articles"][1]["doi"]=bad["articles"][0]["doi"]
            with self.assertRaises(ValueError):
                build_attested_projection("JAE", bad, report)


if __name__ == "__main__":
    unittest.main()
