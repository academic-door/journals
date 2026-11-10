"""LABECO 103(C) fold-multiplier regression; strict semantics must stay fail-closed.

The publisher abstract for DOI 10.1016/j.labeco.2026.102970 reports a
"thirteen-fold" productivity increase. Valid Chinese translations must not
be rejected for writing the same quantity as "十三倍" or "13倍".
"""
from __future__ import annotations

from collections import Counter
import unittest

from scripts.translate_issue import (
    TranslationError,
    _semantic_numbers,
    resolve_semantic_quantities,
    validate_translation,
)

SOURCE = (
    "Output per worker rose thirteen-fold between 1980 and 2018, "
    "while a measured wage premium changed from 8% to 17%."
)
TARGET = (
    "从1980年至2018年，工人平均产出增长至原来的十三倍，"
    "而测得的工资溢价由8%上升至17%。"
)


class LABECOWrittenFoldSemanticTests(unittest.TestCase):
    def test_exact_writing_and_chinese_cardinal_agree(self) -> None:
        expected = Counter({"13": 1, "1980": 1, "2018": 1, "8%": 1, "17%": 1})
        self.assertEqual(Counter(_semantic_numbers(SOURCE)), expected)
        self.assertEqual(Counter(_semantic_numbers(TARGET)), expected)
        english, chinese = resolve_semantic_quantities(SOURCE, TARGET)
        self.assertEqual(english, chinese)

    def test_strict_translation_accepts_exact_fold(self) -> None:
        article = {"title_en": "Productivity and wages", "abstract_en": SOURCE}
        translated = {"title_cn": "生产率与工资", "abstract_cn": TARGET}
        validate_translation(article, translated)

    def test_wrong_fold_still_rejected(self) -> None:
        article = {"title_en": "Productivity and wages", "abstract_en": SOURCE}
        translated = {
            "title_cn": "生产率与工资",
            "abstract_cn": TARGET.replace("十三倍", "十二倍"),
        }
        with self.assertRaisesRegex(TranslationError, "semantic numeric quantities"):
            validate_translation(article, translated)

    def test_fused_and_hyphenated_folds_stay_precise(self) -> None:
        cases = {
            "a thousandfold increase": ["1000"],
            "a thirteen-fold increase": ["13"],
            "a thirteenfold increase": ["13"],
            "a twentyfold increase": ["20"],
            "a twenty-fold increase": ["20"],
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(_semantic_numbers(source), expected)


if __name__ == "__main__":
    unittest.main()
