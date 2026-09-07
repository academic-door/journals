from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.translate_issue import TranslationError, validate_translation


def _article(abstract_en: str) -> dict[str, str]:
    return {
        "title_en": "Numeric translation integration test",
        "abstract_en": abstract_en,
        "article_type": "research-article",
    }


def _translated(abstract_cn: str) -> dict[str, str]:
    return {
        "title_cn": "数值翻译集成测试",
        "abstract_cn": abstract_cn,
    }


class StageCProductionAuthorityTests(unittest.TestCase):
    def test_validate_translation_accepts_semantic_equivalents(self) -> None:
        cases = [
            (
                "Customers bear $10.4 billion in additional annual hedging costs under the policy.",
                "研究结果表明，在该政策下，客户每年承担104亿美元的额外对冲成本，其他结论保持不变。",
            ),
            (
                "The program reaches 100 million people after two decades of expansion.",
                "研究结果表明，该项目经过二十年的扩张后覆盖1亿人，并保持其余结论不变。",
            ),
        ]
        for source, translated in cases:
            with self.subTest(source=source):
                validate_translation(_article(source), _translated(translated))

    def test_validate_translation_rejects_true_numeric_corruption(self) -> None:
        cases = [
            (
                "The program spends $1.0 million each year on administration.",
                "研究结果表明，该项目每年在管理上支出1.0万美元，其他结论保持不变。",
            ),
            (
                "The reform is associated with a 0.7–1 percentage point increase in participation.",
                "研穵结果表明，该改革与参与率提高0.7至2个百分点相关，其他结论保持不变。",
            ),
        ]
        for source, translated in cases:
            with self.subTest(source=source):
                with self.assertRaises(TranslationError):
                    validate_translation(_article(source), _translated(translated))


if __name__ == "__main__":
    unittest.main()
