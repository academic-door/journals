import copy
import json
from pathlib import Path
import unittest

from scripts.import_official_roster_evidence import apply_evidence, validate_evidence, _normalized_title
from scripts.official_current_roster import apply_current_roster
from scripts.update_journals import ROOT

PATH = "data/provenance/official-rosters/sciencedirect/wd-208-c.json"
CONFIG = {"id": "wd", "official_roster_evidence_path": PATH}


class WD208OfficialOrderTests(unittest.TestCase):
    def setUp(self):
        self.e = json.loads((ROOT / PATH).read_text(encoding="utf-8"))
        template = json.loads((ROOT / "public/api/v1/journals/wd/issues/current.json").read_text(encoding="utf-8"))
        template.update(issue_id="wd-208-c", volume="208", articles=[])
        template["quality"] = {"flags": [], "excluded_items": [], "doi_complete":29,
            "authors_complete":29,"abstract_en_complete":29,"translation_complete":0,"duplicate_count":0}
        # Synthetic metadata is deliberately untranslated: the source contract
        # cannot turn these rows READY by fixing their order.
        for r in self.e["items"]:
            template["articles"].append({"paper_id": "doi:"+r["doi"], "doi": r["doi"],
                "title_en": r["title_en"], "title_cn": "", "authors": ["Synthetic Author"],
                "abstract_en": "Synthetic abstract.", "abstract_cn": "", "article_type": "research-article",
                "quality_flags": [],
                "sequence": r["sequence"], "source_url": r["official_article_url"],
                "translation": {"status": "pending", "provider": "", "prompt_version": "", "glossary_version": "1"}})
        template["articles"].reverse()
        self.issue = template

    def test_publisher_33_rows_27_research_two_reviews_four_exclusions(self):
        validate_evidence(self.e)
        self.assertEqual(len(self.e["items"]),29)
        self.assertEqual(len(self.e["excluded_items"]),4)
        self.assertEqual(sum(r["publisher_article_type"]=="research article" for r in self.e["items"]),27)
        self.assertEqual([r["doi"].split(".")[-1] for r in self.e["items"] if r["publisher_article_type"]=="review article"],["107517","107519"])
        self.assertEqual([r["article_type"] for r in self.e["excluded_items"]],["editorial","correction","correction","correction"])

    def test_order_types_and_existing_content_preserved_without_ready(self):
        original=copy.deepcopy(self.issue)
        result=apply_current_roster(self.issue,CONFIG)
        self.assertEqual([a["doi"] for a in result["articles"]],[r["doi"] for r in self.e["items"]])
        self.assertEqual([a["source_sequence"] for a in result["articles"]],list(range(2,31)))
        self.assertEqual(sum(a["publisher_article_type"]=="review article" for a in result["articles"]),2)
        self.assertTrue(all(a["article_type"]=="research-article" for a in result["articles"]))
        self.assertEqual(result["quality"]["official_item_count"],33)
        self.assertNotEqual(result["publication_state"],"ready")
        self.assertEqual(self.issue,original)
        self.assertTrue(all(a["abstract_en"]=="Synthetic abstract." for a in result["articles"]))

    def test_monitor_may_omit_editorial_but_not_paper_or_add_unknown(self):
        detected=[r["doi"] for r in self.e["items"]+self.e["excluded_items"][1:]]
        apply_current_roster(self.issue,CONFIG,detected_dois=detected)
        for values in (detected[1:],detected+["10.1016/j.worlddev.2026.999999"]):
            with self.assertRaises(ValueError):
                apply_current_roster(self.issue,CONFIG,detected_dois=values)

    def test_wrong_title_duplicate_and_missing_metadata_fail_closed(self):
        for kind in ("title","duplicate","authors","abstract"):
            bad=copy.deepcopy(self.issue)
            if kind=="title": bad["articles"][0]["title_en"]="Another paper"
            if kind=="duplicate": bad["articles"].append(copy.deepcopy(bad["articles"][0]))
            if kind=="authors": bad["articles"][0]["authors"]=[]
            if kind=="abstract": bad["articles"][0]["abstract_en"]=""
            with self.assertRaises(ValueError): apply_current_roster(bad,CONFIG)

    def test_unknown_publisher_label_and_unapproved_reorder_rejected(self):
        bad=copy.deepcopy(self.e); bad["items"][0]["publisher_article_type"]="invented"
        with self.assertRaises(ValueError): validate_evidence(bad)
        bad=copy.deepcopy(self.e); bad["allow_archive_reorder"]=False
        with self.assertRaises(ValueError): apply_evidence(self.issue,bad)

    def test_co2_display_whitespace_is_bounded(self):
        self.assertEqual(_normalized_title("CO 2 emissions"),_normalized_title("CO2 emissions"))
        self.assertNotEqual(_normalized_title("CO 3 emissions"),_normalized_title("CO2 emissions"))
