"""Tests for translation numeric canonicalization."""
import unittest
from unittest import mock



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




    def test_currency_prefixed_and_thousands_separated_numbers(self) -> None:
        from scripts.translate_issue import _numbers

        # Currency codes attached directly to the amount (EUR190) and
        # thousand separators (5,503) must be recognized and canonicalized so
        # a translation writing 190欧元 or 5503 does not look invented.
        self.assertIn("190", _numbers("by EUR190 per capita"))
        self.assertIn("5503", _numbers("a EUR5,503 grant"))
        self.assertIn("9093", _numbers("about n = 9,093 income"))
        self.assertNotIn("5,503", _numbers("a EUR5,503 grant"))
        # A spaced currency prefix must not double-count the amount.
        self.assertEqual(
            ["17.88", "9.42"],
            _numbers("worth USD 17.88 and USD 9.42 per acre"),
        )

    def test_currency_amount_translation_passes_validation(self) -> None:
        from scripts.translate_issue import validate_translation

        article = {
            "article_type": "research",
            "title_en": "Fiscal consolidation in Germany",
            "abstract_en": (
                "We investigate the consequences of a large-scale fiscal "
                "consolidation program for German municipalities. Identification "
                "relies on a difference-in-differences approach exploiting "
                "political discretion in the program's assignment rule. We find "
                "that targeted jurisdictions improved their fiscal balance by "
                "EUR190 per capita and year net of the program-induced grants. "
                "Local consolidation strategies differed significantly by "
                "population size, which we rationalize with agglomeration "
                "economies. Spending cuts and tax increases had little effect "
                "on the local economy. However, we detect declines in "
                "population levels and house prices as well as electoral "
                "backlash in smaller municipalities that disproportionally "
                "increased the property tax and cut spending on local public "
                "services."
            ),
        }
        translated = {
            "title_cn": "德国的地方财政整顿",
            "abstract_cn": (
                "我们研究了一项针对德国市政当局的大规模财政整顿计划的后果。"
                "识别依赖于利用计划分配规则中政治自由裁量权的双重差分方法。"
                "我们发现，扣除计划带来的拨款后，目标辖区的财政平衡每年人均"
                "提高了190欧元。当地的整合策略因人口规模而显著不同，我们用"
                "集聚经济对此进行了解释。削减支出和增税对当地经济影响不大。"
                "然而，我们发现在较小城市中，人口水平和房价下降以及选举反弹，"
                "这些城市不成比例地提高了财产税并削减了地方公共服务支出。"
            ),
        }
        validate_translation(article, translated)  # should not raise




    def test_identifier_value_does_not_swallow_same_data_number(self) -> None:
        from scripts.translate_issue import validate_translation

        article = {
            "article_type": "research",
            "title_en": "Faces matter",
            "abstract_en": (
                "Facial profile images increase purchase conversion rates by "
                "6.98 % on average (Study 1). The effect operates through a "
                "causal effect of faces on perceived trustworthiness (Study 3). "
                "The observed effects are not explained by profile "
                "personalisation alone (Studies 2 and 3). Users who upload "
                "facial images may write more helpful reviews (Study 4)."
            ),
        }
        translated = {
            "title_cn": "面孔很重要",
            "abstract_cn": (
                "面部头像照片平均能使购买转化率提高6.98%（研究1）。该效应通过"
                "面孔对可信度感知的因果影响起作用（研究3）。所观察到的效应不能"
                "仅由头像个性化来解释（研究2和3）。上传面部头像的用户可能会撰写"
                "更有帮助的评论（研究4）。"
            ),
        }
        validate_translation(article, translated)  # data "3" must keep counting

    def test_cjk_identifier_label_does_not_fuse_across_title_boundary(self) -> None:
        from scripts.translate_issue import validate_translation, TranslationError

        article = {
            "article_type": "research",
            "title_en": "Old age allowances and cognitive function: A quasi-experimental study",
            "abstract_en": (
                "The population aged 80 and above is the fastest-growing age "
                "group globally. Old age allowances significantly enhance "
                "cognitive function."
            ),
        }
        # Title ends with a CJK identifier label ("研究"); the abstract's
        # leading number must still count as a data value, not an identifier.
        translated_missing = {
            "title_cn": "高龄津贴与认知功能：对中国高龄老人的准实验研究",
            "abstract_cn": "岁及以上人口是全球增长最快的年龄组。高龄津贴显著改善了认知功能。",
        }
        with self.assertRaises(TranslationError):
            validate_translation(article, translated_missing)
        translated_ok = {
            "title_cn": "高龄津贴与认知功能：对中国高龄老人的准实验研究",
            "abstract_cn": "80岁及以上人口是全球增长最快的年龄组。高龄津贴显著改善了认知功能。",
        }
        validate_translation(article, translated_ok)

    def test_section_identifier_number_is_not_invented(self) -> None:
        from scripts.translate_issue import validate_translation

        article = {
            "article_type": "research",
            "title_en": "Graduate medical education subsidies",
            "abstract_en": (
                "We quantify the impact of federal subsidies for graduate "
                "medical education on primary care physician supply by "
                "examining the impact of Section 5503 of the Affordable Care "
                "Act, which increased the number of residents that teaching "
                "hospitals in rural and high-need areas could receive "
                "subsidies for training. Instrumenting for selection into the "
                "program using its eligibility criteria, we find that the "
                "provision increased both recruitment of residents into "
                "primary care and time spent at teaching hospitals in "
                "high-need areas, resulting in a 4.1 percent increase in "
                "primary care physician supply."
            ),
        }
        translated = {
            "title_cn": "研究生医学教育补贴",
            "abstract_cn": (
                "我们量化了联邦研究生医学教育补贴对初级保健医生供给的影响，"
                "考察了《平价医疗法案》第5503条的影响，该条款增加了农村和高需求"
                "地区教学医院可获得培训补贴的住院医师人数。利用其资格标准对项目"
                "选择进行工具变量处理，我们发现该条款提高了初级保健住院医师的"
                "招聘人数以及在高需求地区教学医院的时间，从而使初级保健医生供给"
                "增加了4.1%。"
            ),
        }
        validate_translation(article, translated)  # should not raise




class DeepSeekModelResolutionTests(unittest.TestCase):
    def test_default_model_is_deepseek_v4_flash(self) -> None:
        from scripts.translate_issue import _deepseek_model

        with mock.patch.dict("os.environ", {"DEEPSEEK_MODEL": ""}, clear=False):
            self.assertEqual("deepseek-v4-flash", _deepseek_model())

    def test_env_override_selects_reasoner(self) -> None:
        from scripts.translate_issue import _deepseek_model

        with mock.patch.dict(
            "os.environ", {"DEEPSEEK_MODEL": "deepseek-reasoner"}, clear=False
        ):
            self.assertEqual("deepseek-reasoner", _deepseek_model())

    def test_empty_env_falls_back_to_default(self) -> None:
        from scripts.translate_issue import _deepseek_model

        with mock.patch.dict("os.environ", {"DEEPSEEK_MODEL": "   "}, clear=False):
            self.assertEqual("deepseek-v4-flash", _deepseek_model())



if __name__ == "__main__":
    unittest.main()
