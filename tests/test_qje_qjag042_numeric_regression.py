"""Pinned QJE qjag042 source/cache semantic regression (owner-local evidence)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.translate_issue import (
    TranslationError,
    _source_hash,
    resolve_semantic_quantities,
    validate_translation,
)

ROOT = Path(__file__).resolve().parents[1]
DOI = "10.1093/qje/qjag042"


class QJEGuaranteedIncomeTranslationRegression(unittest.TestCase):
    def source_and_cache(self):
        evidence = json.loads(
            (ROOT / "tests/fixtures/qje_qjag042_source_regression.json").read_text(
                encoding="utf-8"
            )
        )
        cache = json.loads(
            (ROOT / "data/translation-cache/qje.json").read_text(encoding="utf-8")
        )[DOI]
        article = {
            "doi": DOI,
            "article_type": "research-article",
            "title_en": evidence["title_en"],
            "abstract_en": evidence["abstract_en"],
        }
        self.assertEqual(evidence["expected_source_sha256"], _source_hash(article))
        self.assertEqual(_source_hash(article), cache["source_hash"])
        return article, cache

    def test_pinned_valid_translation_passes_production_semantic_gate(self):
        article, cache = self.source_and_cache()
        source_numbers, target_numbers = resolve_semantic_quantities(
            article["title_en"] + "\n" + article["abstract_en"],
            cache["title_cn"] + "\n" + cache["abstract_cn"],
        )
        self.assertEqual(
            source_numbers,
            target_numbers,
            "QJE semantic quantities mismatch: "
            + f"source={dict(source_numbers)}, translation={dict(target_numbers)}",
        )
        validate_translation(article, cache)

    def test_genuinely_wrong_quantity_still_fails_closed(self):
        article, cache = self.source_and_cache()
        wrong = dict(cache)
        self.assertIn("4.2", wrong["abstract_cn"])
        wrong["abstract_cn"] = wrong["abstract_cn"].replace("4.2", "5.2", 1)
        with self.assertRaises(TranslationError):
            validate_translation(article, wrong)


if __name__ == "__main__":
    unittest.main()
