"""Tests for translation numeric canonicalization."""
import unittest



class ChineseNumeralCanonicalizationTests(unittest.TestCase):
    def test_parse_chinese_numerals(self) -> None:
        from scripts.translate_issue import _parse_chinese_numeral

        self.assertEqual(80, _parse_chinese_numeral("八十"))
        self.assertEqual(123, _parse_chinese_numeral("一百二十三"))
        self.assertEqual(2025, _parse_chinese_numeral("二〇二五"))
        self.assertEqual(200000, _parse_chinese_numeral("二十万"))
        self.assertEqual(12345, _parse_chinese_numeral("一万二千三百四十五"))
        self.assertEqual(10, _parse_chinese_numeral("十"))

    def test_canonicalize_chinese_numerals(self) -> None:
        from scripts.translate_issue import _canonicalize_chinese_numerals

        self.assertEqual(
            "80岁及以上", _canonicalize_chinese_numerals("aged 80 and above", "八十岁及以上")
        )
        self.assertEqual(
            "80%的样本", _canonicalize_chinese_numerals("80 percent of the sample", "百分之八十的样本")
        )
        self.assertEqual(
            "2025年", _canonicalize_chinese_numerals("since 2025", "二〇二五年")
        )
        self.assertEqual(
            "200000人口", _canonicalize_chinese_numerals("200000 residents", "二十万人口")
        )
        # A single Chinese digit rendering an English count word stays as-is.
        self.assertEqual(
            "两种收益类型参与者参与 2×2 博弈",
            _canonicalize_chinese_numerals(
                "two payoff types of players in 2 × 2 games",
                "两种收益类型参与者参与 2×2 博弈",
            ),
        )

    def test_chinese_numeral_translation_passes_numeric_validation(self) -> None:
        from scripts.translate_issue import validate_translation

        article = {
            "article_type": "research",
            "title_en": "Old age allowances and cognitive function",
            "abstract_en": (
                "The population aged 80 and above represents the "
                "fastest-growing age group globally and has the greatest need "
                "for maintaining cognitive health. This paper investigates the "
                "causal impact of old age allowances on cognitive function "
                "among China's oldest-old. Exploiting a regression "
                "discontinuity design around eligibility thresholds, we "
                "demonstrate that these allowances significantly enhance "
                "cognitive function. Our empirical analysis indicates that "
                "increased healthcare utilization and improvements in "
                "physical health are unlikely to be the primary mechanisms. "
                "We provide evidence that old age allowances increase food "
                "consumption, reduce labor participation, expand social "
                "engagement, strengthen personal relationships, and alleviate "
                "depressive symptoms."
            ),
        }
        translated = {
            "title_cn": "高龄津贴与认知功能",
            "abstract_cn": (
                "80岁及以上人口是增长最快的年龄组，对维持认知健康的需求最大。"
                "本文研究高龄津贴对中国高龄老人认知功能的因果影响。利用资格阈值"
                "附近的断点回归设计，我们证明这些津贴显著增强了认知功能。实证分析"
                "表明医疗利用增加和身体健康改善不太可能是主要机制。我们提供的证据"
                "表明高龄津贴增加了食品消费、减少了劳动参与、扩大了社会参与、"
                "加强了人际关系并减轻了抑郁症状。"
            ),
        }
        validate_translation(article, translated)  # should not raise

        # Pipeline order: normalize written/Chinese numerals back to Arabic,
        # canonicalize, then validate (mirrors request_translation).
        from scripts.translate_issue import (
            _canonicalize_arabic_numbers,
            _normalize_written_number_translations,
        )

        chinese_rendered = dict(translated)
        chinese_rendered["abstract_cn"] = translated["abstract_cn"].replace("80岁", "八十岁")
        normalized = _normalize_written_number_translations(
            article["abstract_en"], chinese_rendered["abstract_cn"]
        )
        normalized = _canonicalize_arabic_numbers(
            article["abstract_en"], normalized
        )
        chinese_rendered["abstract_cn"] = normalized
        validate_translation(article, chinese_rendered)



if __name__ == "__main__":
    unittest.main()
