from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.correct_b8_ijio_period import DOIS, OFFICIAL_PERIOD, eligible, repair

class IJIOOfficialPeriodCorrectionTests(unittest.TestCase):
    def payload(self):
        return {
            "journal_id":"ijio", "issue_id":"ijio-108-c",
            "source_url":"https://ideas.repec.org/s/eee/indorg.html",
            "source_status":"publisher_verified", "publication_state":"ready",
            "publication_date":"2026", "public_mode":"metadata_first_rights_qualified_v1",
            "content_status":"metadata_complete",
            "quality":{"roster_authority":"repec-publisher-supplied","roster_match":True,"order_preserved":True,"excluded_items":[]},
            "public_content":{"metadata_articles":6,"rights_withheld_articles":6,"abstracts_en_visible":0,"translations_cn_visible":0},
            "articles":[{"sequence":i,"doi":d,"article_type":"research-article",
                         "public_rights":{"status":"withheld"},
                         "translation":{"status":"rights_withheld"},
                         "title_en":"Synthetic test", "authors":["Test Author"]}
                        for i,d in enumerate(DOIS,1)]
        }

    def seed(self,root,payload):
        parent=root/"public/api/v1/journals/ijio/issues"
        parent.mkdir(parents=True)
        for name in ("current.json","ijio-108-c.json"):
            (parent/name).write_text(json.dumps(payload),encoding="utf-8")
        return parent

    def test_exact_period_correction_is_idempotent_and_preserves_all_other_fields(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            orig=self.payload()
            p=self.seed(root,orig)
            changed=repair(root)
            self.assertEqual(2,len(changed))
            result=json.loads((p/"current.json").read_text())
            self.assertEqual(OFFICIAL_PERIOD,result["publication_date"])
            restored=copy.deepcopy(result)
            restored["publication_date"]="2026"
            self.assertEqual(orig,restored)
            self.assertEqual(result,json.loads((p/"ijio-108-c.json").read_text()))
            self.assertEqual([],repair(root))

    def test_rejects_diverged_immutable_seed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            p=self.seed(root,self.payload())
            old=json.loads((p/"ijio-108-c.json").read_text())
            old["issue_id"]="ijio-999-c"
            (p/"ijio-108-c.json").write_text(json.dumps(old))
            with self.assertRaisesRegex(ValueError,"diverged"):
                repair(root)

    def test_rejects_unlicensed_public_content_and_unexpected_period(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            v=self.payload()
            v["articles"][0]["abstract_en"]="Unlicensed"
            p=self.seed(root,v)
            with self.assertRaisesRegex(ValueError,"rights"):
                repair(root)
            v=self.payload()
            v["publication_date"]="September 2026"
            self.seed(root,v)
            with self.assertRaisesRegex(ValueError,"unexpected"):
                repair(root)

    def test_rejects_incorrect_doi_sequence(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            v=self.payload()
            v["articles"][0]["doi"]="10.0000/wrong"
            self.seed(root,v)
            with self.assertRaises(ValueError):
                repair(root)


if __name__=="__main__":
    unittest.main()
