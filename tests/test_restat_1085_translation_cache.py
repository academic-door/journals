"""RESTAT 108(5) exact-source cached translation, numeric and provenance regression."""

import copy
import json
from pathlib import Path
import unittest

from scripts.translate_issue import (
    TranslationError, _source_hash, validate_translation,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/provenance/official-rosters/restat/restat-108-5.json"
CACHE = ROOT / "data/translation-cache/restat.json"
DOI = "10.1162/rest_a_01469"


class Restat1085TranslationTests(unittest.TestCase):
    def setUp(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        item = next(x for x in evidence["items"] if x["doi"] == DOI)
        self.article = {
            "doi": DOI,
            "article_type": "research-article",
            "title_en": item["title_en"],
            "abstract_en": item["abstract_en"],
        }
        self.cache = json.loads(CACHE.read_text(encoding="utf-8"))[DOI]

    def test_actual_cached_translation_matches_verified_source(self):
        self.assertEqual(_source_hash(self.article), self.cache["source_hash"])
        self.assertIn("1.1 p.p.", self.article["abstract_en"])
        self.assertIn("1.1个百分点", self.cache["abstract_cn"])
        self.assertIn("9.8%", self.cache["abstract_cn"])
        self.assertNotIn("1.1%", self.cache["abstract_cn"])
        validate_translation(self.article, self.cache)

    def test_unit_corruption_and_translation_hash_mismatch_are_rejected(self):
        wrong = copy.deepcopy(self.cache)
        wrong["abstract_cn"] = wrong["abstract_cn"].replace("1.1个百分点", "1.1%")
        with self.assertRaisesRegex(TranslationError, "percentage-point unit"):
            validate_translation(self.article, wrong)
        self.assertNotEqual(_source_hash(self.article), "0" * 64)


if __name__ == "__main__":
    unittest.main()
