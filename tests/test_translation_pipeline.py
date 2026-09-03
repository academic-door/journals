from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import requests

from scripts.translate_issue import (
    TranslationError,
    _canonicalize_arabic_numbers,
    _extract_json,
    _month_numbers,
    _normalize_written_number_translations,
    _numbers,
    _protect_numbers,
    _repair_google_artifacts,
    _restore_numbers,
    _translation_numeric_multiset,
    _written_number_values,
    request_deepseek_translation,
    request_translation,
    translate_missing,
    validate_translation,
)


ARTICLE = {
    "doi": "10.0000/example",
    "title_en": "A Test of Policy",
    "abstract_en": (
        "We study 96 policies and find that emissions fall by 12.5% while "
        "welfare rises. The estimates preserve the complete research design."
    ),
}


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title_cn": "政策检验",
                                "abstract_cn": (
                                    "本文研究96项政策，发现排放下降12.5%，同时福利提高。"
                                    "估计过程完整保留了论文的研究设计、变量定义与结论方向，"
                                    "并忠实呈现原始摘要中的经验结果。"
                                ),
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }


class FakeSession:
    def post(self, *args, **kwargs) -> FakeResponse:
        return FakeResponse()


class ExplodingSession:
    def post(self, *args, **kwargs) -> FakeResponse:
        raise AssertionError("valid cache entry must not call the model")


class ForbiddenResponse:
    status_code = 403

    def raise_for_status(self) -> None:
        error = requests.HTTPError("forbidden")
        error.response = self
        raise error


class GoogleResponse:
    def __init__(self, value: str) -> None:
        self.value = value

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list:
        return [[[self.value, "source", None, None]]]


class FallbackSession:
    def post(self, url, *args, **kwargs):
        if "models.github.ai" in url:
            return ForbiddenResponse()
        source = kwargs["data"]["q"]
        return GoogleResponse(
            "政策检验\n[[9876543210123456789]]\n"
            "本文研究[[96]]项政策，发现排放下降[[12.5%]]，同时福利提高。"
            "估计过程完整保留了论文的研究设计、变量定义与结论方向，"
            "并忠实呈现原始摘要中的经验结果。"
        )


class TerminalResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        error = requests.HTTPError(f"HTTP {self.status_code}")
        error.response = self
        raise error


class CircuitBreakingSession:
    def __init__(self) -> None:
        self.github_requests = 0
        self.google_requests = 0

    def post(self, url, *args, **kwargs):
        if "models.github.ai" in url:
            self.github_requests += 1
            return TerminalResponse(410)
        self.google_requests += 1
        return TerminalResponse(429)


class TranslationPipelineTests(unittest.TestCase):
    def test_extracts_fenced_json(self) -> None:
        data = _extract_json('```json\n{"title_cn":"测试","abstract_cn":"摘要"}\n```')
        self.assertEqual(data["title_cn"], "测试")

    def test_github_models_request_uses_current_api_version(self) -> None:
        class CapturingSession:
            def __init__(self) -> None:
                self.headers = {}

            def post(self, *args, **kwargs) -> FakeResponse:
                self.headers = kwargs["headers"]
                return FakeResponse()

        session = CapturingSession()
        request_translation(ARTICLE, token="test-token", session=session)
        self.assertEqual(session.headers["X-GitHub-Api-Version"], "2026-03-10")

    def test_deepseek_request_uses_non_thinking_json_output(self) -> None:
        class CapturingSession:
            def __init__(self) -> None:
                self.payload = {}

            def post(self, *args, **kwargs) -> FakeResponse:
                self.payload = kwargs["json"]
                return FakeResponse()

        session = CapturingSession()
        request_translation(
            ARTICLE,
            token="test-token",
            model="deepseek-v4-flash",
            provider_name="deepseek",
            session=session,
            json_output=True,
            disable_thinking=True,
        )
        self.assertEqual(
            session.payload["response_format"], {"type": "json_object"}
        )
        self.assertEqual(session.payload["thinking"], {"type": "disabled"})

    def test_rejects_missing_numbers(self) -> None:
        with self.assertRaises(TranslationError):
            validate_translation(
                ARTICLE,
                {
                    "title_cn": "政策检验",
                    "abstract_cn": "本文研究多项政策并发现排放下降，同时福利提高。" * 5,
                },
            )

    def test_rejects_substring_and_duplicate_number_mismatches(self) -> None:
        article = {
            **ARTICLE,
            "title_en": "Evidence from 5 cities",
            "abstract_en": "We compare 5 cities with 15 regions and 5 policy rounds.",
        }
        with self.assertRaises(TranslationError):
            validate_translation(
                article,
                {
                    "title_cn": "来自15个城市的证据",
                    "abstract_cn": (
                        "本文比较15个地区与5轮政策，并完整说明研究设计、"
                        "识别策略、变量定义和主要经验结论。"
                    ),
                },
            )

    def test_rejects_added_numeric_values(self) -> None:
        with self.assertRaises(TranslationError):
            validate_translation(
                ARTICLE,
                {
                    "title_cn": "2026年政策检验",
                    "abstract_cn": (
                        "本文研究96项政策，发现排放下降12.5%，同时福利提高。"
                        "估计过程完整保留了论文的研究设计、变量定义与结论方向，"
                        "并忠实呈现原始摘要中的经验结果。"
                    ),
                },
            )



    def test_month_period_notation_counts_as_date_numbers(self) -> None:
        article = {
            "doi": "10.0000/example",
            "title_en": "Tail risk contagion",
            "abstract_en": (
                "Using data from 2006M07 to 2023M03, this study examines tail "
                "risk contagion across electricity markets during crises and "
                "documents the complete research design and policy implications."
            ),
        }
        validate_translation(
            article,
            {
                "title_cn": "尾部风险传染",
                "abstract_cn": (
                    "本研究使用2006年7月至2023年3月的数据，考察危机期间电力市场"
                    "之间的尾部风险传染，并完整说明研究设计与政策含义。"
                ),
            },
        )
        self.assertEqual(
            sorted(_month_numbers("from 2006M07 to 2023M03")),
            ["2006", "2023", "3", "7"],
        )

    def test_month_names_count_as_numeric_dates(self) -> None:
        article = {
            "doi": "10.0000/example",
            "title_en": "Tail risk contagion",
            "abstract_en": (
                "From July 2006 through March 2023 we measure tail risk "
                "contagion across electricity markets and document persistent "
                "crisis-period linkages with a complete research design."
            ),
        }
        validate_translation(
            article,
            {
                "title_cn": "尾部风险传染",
                "abstract_cn": (
                    "从2006年7月至2023年3月，我们衡量电力市场之间的尾部风险传染，"
                    "记录危机时期持续存在的联动关系，并完整保留研究设计。"
                ),
            },
        )
        self.assertEqual(
            sorted(_month_numbers("July 2006 through March 2023")),
            ["3", "7"],
        )

    def test_protects_and_restores_numbers_without_reformatting(self) -> None:
        source = (
            "In January 2026 we estimate one effect: "
            "1.15, 509, 7, and 9.3 percent."
        )
        protected, replacements = _protect_numbers(source)
        self.assertIn("[[1.15]]", protected)
        self.assertIn("[[9.3%]]", protected)
        restored = _restore_numbers(protected, replacements)
        self.assertEqual(
            restored,
            "In 一月 2026 we estimate one effect: 1.15, 509, 7, and 9.3%.",
        )

    def test_numeric_placeholders_keep_quantity_semantics(self) -> None:
        source = (
            "Costs rose roughly 170 percent in 2022, while arrests fell "
            "15 percent."
        )
        protected, replacements = _protect_numbers(source)
        self.assertEqual(
            protected,
            "Costs rose roughly [[170%]] in [[2022]], while arrests fell "
            "[[15%]].",
        )
        self.assertEqual(_restore_numbers(protected, replacements), source.replace(
            "170 percent", "170%"
        ).replace("15 percent", "15%"))

    def test_currency_and_scientific_suffix_numbers_are_preserved(self) -> None:
        source = "Payments were N2500, N4000, or N10000 and emissions were CO2."
        self.assertEqual(_numbers(source), [])
        protected, replacements = _protect_numbers(source)
        self.assertIn("[[N2500]]", protected)
        self.assertIn("[[CO2]]", protected)
        self.assertEqual(_restore_numbers(protected, replacements), source)

    def test_numeric_protection_keeps_alphanumeric_tokens_whole(self) -> None:
        protected, replacements = _protect_numbers(
            "N2500 transfers and CO2 emissions from 2003 to 2010"
        )

        self.assertIn("[[N2500]]", protected)
        self.assertIn("[[CO2]]", protected)
        self.assertNotIn("CO[[2]]", protected)
        self.assertEqual(
            _restore_numbers(protected, replacements),
            "N2500 transfers and CO2 emissions from 2003 to 2010",
        )

    def test_written_number_words_are_left_for_natural_translation(self) -> None:
        protected, _replacements = _protect_numbers("two experimental arms")

        self.assertEqual(protected, "two experimental arms")

    def test_numeric_range_is_wrapped_once(self) -> None:
        protected, replacements = _protect_numbers("Benefits apply at ages 0–2.")
        self.assertEqual(protected, "Benefits apply at ages [[0-2]].")
        self.assertEqual(_restore_numbers(protected, replacements), "Benefits apply at ages 0-2.")

    def test_restores_percent_sign_dropped_inside_preserved_brackets(self) -> None:
        self.assertEqual(
            _restore_numbers("收入提高[[27]]。", {"[[27%]]": "27%"}),
            "收入提高27%。",
        )

    def test_does_not_guess_changed_or_ambiguous_bracketed_values(self) -> None:
        self.assertEqual(
            _restore_numbers(
                "结果为[[28]]和[[2 %]]。",
                {"[[27%]]": "27%", "[[2]]": "2", "[[2%]]": "2%"},
            ),
            "结果为[[28]]和[[2 %]]。",
        )

    def test_normalizes_months_and_written_percentages_before_validation(self) -> None:
        source = (
            "From December 2025, adoption rose from three to six percent "
            "and later remained below two percent."
        )
        translated = "自2025年12月起，采用率从3%上升至6%，随后保持在2%以下。"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertEqual(
            normalized,
            "自2025年十二月起，采用率从百分之三上升至百分之六，"
            "随后保持在百分之二以下。",
        )
        self.assertEqual(_numbers(source), _numbers(normalized))

    def test_hyphenated_written_percentages_match_arabic_translation(self) -> None:
        source = (
            "Fifty-three percent of account holders stayed with the old product, "
            "and the study documents the complete decision process."
        )
        translated = (
            "百分之五十三的账户持有人继续使用旧产品，"
            "研究还完整记录了决策过程。"
        )
        normalized = _normalize_written_number_translations(source, translated)
        validate_translation(
            {
                "title_en": "Financial decisions",
                "abstract_en": source,
                "article_type": "research-article",
            },
            {"title_cn": "金融决策", "abstract_cn": normalized},
        )

    def test_written_scale_year_matches_arabic_translation(self) -> None:
        source = (
            "The reform was introduced in two thousand and ten, and the paper "
            "documents the institutional background and observed outcomes."
        )
        translated = (
            "这项改革于2010年推出，本文记录了制度背景和观察到的结果。"
        )
        validate_translation(
            {
                "title_en": "Policy reform",
                "abstract_en": source,
                "article_type": "research-article",
            },
            {"title_cn": "政策改革", "abstract_cn": translated},
        )

    def test_chinese_number_spacing_and_percentage_ranges_are_equivalent(self) -> None:
        source = (
            "The program reached nearly five million households, and adoption "
            "rose from three to six percent."
        )
        translated = (
            "该项目覆盖了近 五 百万户家庭，采用率从三到六个百分点上升。"
        )
        validate_translation(
            {
                "title_en": "Program reach",
                "abstract_en": source,
                "article_type": "research-article",
            },
            {"title_cn": "项目覆盖", "abstract_cn": translated},
        )

    def test_wrong_written_percentage_still_fails(self) -> None:
        with self.assertRaises(TranslationError):
            validate_translation(
                {
                    "title_en": "Loan repayment",
                    "abstract_en": (
                        "Ninety-eight percent of beneficiaries repaid the loan "
                        "after harvest, according to the study results."
                    ),
                    "article_type": "research-article",
                },
                {
                    "title_cn": "贷款偿还",
                    "abstract_cn": (
                        "研究结果显示，百分之九十的受益人在收获后偿还了贷款，"
                        "这一结论来自完整的样本分析。"
                    ),
                },
            )

    def test_unicode_hyphen_written_percentage_keeps_compound_value(self) -> None:
        source = (
            "Ninety‑eight percent of beneficiaries repaid the loan after harvest, "
            "according to the study's complete results."
        )
        translated = (
            "收获后，百分之九十八的受益人偿还了贷款；研究还完整记录了这一结果及其含义。"
        )
        validate_translation(
            {
                "title_en": "Loan repayment",
                "abstract_en": source,
                "article_type": "research-article",
            },
            {"title_cn": "贷款偿还", "abstract_cn": translated},
        )

    def test_written_century_ordinal_does_not_look_like_an_added_number(self) -> None:
        source = (
            "Spain was rich around 1500, but prices rose by 200% by the "
            "mid-seventeenth century and GDP was 40% lower by 1750."
        )
        translated = (
            "西班牙在1500年前后十分富裕，但到17世纪中叶价格上涨了200%，"
            "到1750年国内生产总值下降了40%。"
        )
        normalized = _normalize_written_number_translations(source, translated)
        self.assertIn("十七世纪中叶", normalized)
        self.assertCountEqual(_numbers(source), _numbers(normalized))

    def test_hyphenated_english_number_word_does_not_split_suffix_digit(self) -> None:
        source = "The sample follows sixty-five-year-old participants through 2013."
        self.assertEqual(_numbers(source), ["2013"])
        self.assertEqual(_numbers("The sample covers under-65 adults."), ["65"])

    def test_written_decades_keep_their_numeric_horizon(self) -> None:
        self.assertEqual(_written_number_values("in less than two decades"), ["20"])

    def test_hyphenated_and_coordinated_centuries_preserve_numeric_facts(self) -> None:
        article = {
            "title_en": "When Did Growth Begin?",
            "abstract_en": (
                "Growth began in the seventeenth-century countryside. "
                "Productivity rose in the eighteenth and early nineteenth "
                "centuries, reaching 5% by 1860."
            ),
            "article_type": "research-article",
        }
        validate_translation(
            article,
            {
                "title_cn": "增长何时开始？",
                "abstract_cn": (
                    "增长始于17世纪的乡村。生产率在18世纪和19世纪初上升，"
                    "到1860年达到5%。这段摘要保留了全部研究事实与结论。"
                ),
            },
        )

    def test_repairs_google_age_ranges_and_percent_phrases(self) -> None:
        source = (
            "Using birth certificates linked to administrative records, we find "
            "low-income families of infants born just below the cutoff receive "
            "higher monthly cash benefits (equal to 27 percent of family income) "
            "at ages 0–2 with smaller benefits continuing through age 10."
        )
        translated = (
            "使用与行政记录相关联的出生证明，我们发现出生于临界值以下的婴儿的低收入家庭"
            "在27%0岁时每月获得较高的现金福利（相当于家庭收入的2），而在整个至10岁期间福利一直较少。"
        )
        repaired = _repair_google_artifacts(source, translated)
        self.assertIn("0至2岁时", repaired)
        self.assertIn("相当于家庭收入的27%", repaired)
        self.assertIn("较小的福利持续至10岁", repaired)

    def test_identifier_label_numbers_are_not_numeric_results(self) -> None:
        source = "Across two experiments (N = 1750), Study 2 used a Random Dictator mechanism."
        translated = "在两项实验（N=1750）中，研究2使用了随机独裁者机制。"
        self.assertEqual(_numbers(source), ["1750"])
        self.assertEqual(_numbers(translated), ["1750"])
        article = {
            "doi": "10.0000/envy",
            "title_en": "Envy in the ballot box",
            "abstract_en": source,
        }
        validate_translation(
            article,
            {
                "title_cn": "投票箱中的嫉妒",
                "abstract_cn": (
                    "在两项实验（N=1750）中，研究2使用了随机独裁者机制，"
                    "本文完整说明研究设计、变量定义与主要经验结论。"
                ),
            },
        )

    def test_chinese_one_in_hundred_year_idiom_matches_source(self) -> None:
        source = "These were once 1-in-100-year events."
        self.assertEqual(
            Counter({"1": 1, "100": 1}),
            _translation_numeric_multiset(source, "这些曾经是百年一遇的事件。"),
        )

    def test_hyphenated_survey_identifier_is_not_a_result_number(self) -> None:
        self.assertEqual([], _numbers("We use NFHS-4 data."))

    def test_unity_is_equivalent_to_one(self) -> None:
        self.assertEqual(["1"], _written_number_values("risk aversion exceeds unity"))

    def test_one_percentage_point_is_a_bare_quantity(self) -> None:
        self.assertEqual(["1"], _written_number_values("a one-percentage-point increase"))

    def test_fit_for_identifier_is_not_a_result_number(self) -> None:
        self.assertEqual([], _numbers("The Fit-for-55 package was adopted."))

    def test_unicode_hyphenated_model_identifier_matches_ascii_translation(self) -> None:
        source = "The result is confirmed for DICE‐2023."
        translated = "该结果在DICE-2023中得到证实。"
        self.assertEqual([], _numbers(source))
        self.assertEqual([], _numbers(translated))

    def test_decimal_chinese_million_keeps_coefficient(self) -> None:
        source = "a million truckloads and 1.4 million tomatoes"
        self.assertEqual(
            Counter({"1000000": 1, "1.4": 1}),
            _translation_numeric_multiset(source, "百万卡车装载量和1.4百万个番茄"),
        )

    def test_arabic_scale_quantities_match_chinese_scales(self) -> None:
        source = "The study covers 600 million sessions and 21 million passengers."
        translated = "该研究覆盖6亿次会话和2100万名乘客。"
        self.assertEqual(
            Counter({"600000000": 1, "21000000": 1}),
            _translation_numeric_multiset(source, translated),
        )

    def test_million_and_billion_chinese_scale_variants_match(self) -> None:
        source = "The hospital received $3 billion and studied 10 million cases."
        translated = "该医院获得3十亿资金并研究了10百万个病例。"
        self.assertEqual(
            Counter({"3000000000": 1, "10000000": 1}),
            _translation_numeric_multiset(source, translated),
        )

    def test_latex_percent_and_decimal_scale_are_not_split(self) -> None:
        source = r"Emissions fell by 43$\%$ and savings exceeded $1.2 billion."
        self.assertEqual(_numbers(source), ["43%", "1.2"])

    def test_around_and_legal_labels_do_not_hide_numeric_results(self) -> None:
        # "around" must not be treated as the identifier label "round".
        self.assertEqual(_numbers("revenue rose by around 10 percent"), ["10%"])
        self.assertEqual(_numbers("around 5% increase"), ["5%"])
        self.assertEqual(_numbers("Around 55% of subjects"), ["55%"])
        self.assertEqual(_numbers("around 2015: structural shift"), ["2015"])
        self.assertEqual(_numbers("reduced to around 1500 ha"), ["1500"])
        # Legal/statutory labels stay exempt in both languages.
        self.assertEqual(_numbers("Chapter 11 reorganization"), [])
        self.assertEqual(_numbers("Assembly Bill 52 compliance"), [])
        self.assertEqual(_numbers("第11章进行重组"), [])
        self.assertEqual(_numbers("在第52号议会法案下"), [])
        # Chinese verb+statistic is not a label ("进行2.1%").
        self.assertEqual(_numbers("进行2.1%的缩尾处理"), ["2.1%"])
        # Chinese labels with optional whitespace and 第N轮 forms.
        self.assertEqual(_numbers("实验 1 比较了"), [])
        self.assertEqual(_numbers("研究2使用了"), [])
        self.assertEqual(_numbers("第2轮n = 9,093笔交易"), ["9093"])
        # Years remain counted even after Chinese label nouns.
        self.assertEqual(_numbers("研究1959古巴革命"), ["1959"])

    def test_normalize_does_not_rewrite_percent_or_decimal_digits(self) -> None:
        source = (
            "One year later GDP fell by 1% to 3.3% and 4.5% in two scenarios."
        )
        translated = "一年后GDP下降1%至3.3%和4.5%，涉及两种情景。"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertEqual(_numbers(normalized), ["1%", "3.3%", "4.5%"])

    def test_canonicalize_keeps_reordered_but_faithful_digits(self) -> None:
        source = (
            "GDP remains about 2% lower over the medium run (5-7 years) "
            "and does not recover within the 10-year horizon."
        )
        translated = "GDP在中期（5-7年）仍保持约2%较低，在10年内不会恢复。"
        self.assertEqual(
            _canonicalize_arabic_numbers(source, translated),
            translated,
        )

    def test_normalizes_half_a_million_google_rendering(self) -> None:
        source = (
            "The 1975 eruption sparked the return of half a million "
            "retornados to Portugal."
        )
        translated = "1975年的爆发促使50万回归者返回葡萄牙。"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertIn("五十万", normalized)
        self.assertEqual(_numbers(normalized), ["1975"])

    def test_written_words_do_not_rewrite_source_arabic_data(self) -> None:
        # "three-sector model" and "one-time shock" must not rewrite the
        # Arabic data values 3/1 that already exist in the source.
        source = (
            "In a three-sector model, as growth declines from 3 to −1 percent, "
            "a one-time shock of 3 percentage points lowers output by 10%."
        )
        translated = (
            "在三部门模型中，随着增长从3下降到-1%，一次性冲击使3个百分点的产出下降10%。"
        )
        normalized = _normalize_written_number_translations(source, translated)
        self.assertEqual(
            _numbers(normalized),
            ["3", "-1%", "3", "10%"],
        )

    def test_parenthesized_list_numbers_survive_normalization(self) -> None:
        source = (
            "Students were (1) introduced to resilience thinking, "
            "(2) worked in groups, and (3) discussed strategies."
        )
        translated = "学生（1）学习了复原力思维，（2）分组工作，（3）讨论策略。"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertEqual(_numbers(normalized), ["1", "2", "3"])

    def test_quarter_year_suffix_is_not_an_invented_year(self) -> None:
        source = "We update the database through 2025Q2, using 55 years of data."
        translated = "我们使用55年的数据，将数据库更新至2025年第二季度。"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertIn("2025Q2", normalized)
        self.assertEqual(_numbers(normalized), ["55"])

    def test_spaced_percent_sign_counts_as_percent(self) -> None:
        self.assertEqual(_numbers("biased toward 0 % in each frame"), ["0%"])
        self.assertEqual(_numbers("probability close to 0%"), ["0%"])

    def test_month_abbreviations_normalize_to_chinese_months(self) -> None:
        source = "from Sep. 2019 to Sep. 2020, we tracked all facemasks"
        translated = "从2019年9月至2020年9月，我们跟踪了所有口罩"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertIn("九月", normalized)
        self.assertEqual(_numbers(normalized), ["2019", "2020"])

    def test_plural_studies_and_billion_amounts_are_normalized(self) -> None:
        self.assertEqual(_numbers("explained by profile personalisation (Studies 2 and 3)"), ["3"])
        source = "caused $20bn of deadweight loss in 2022 and 2023"
        translated = "在2022年和2023年造成了200亿美元的净损失"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertIn("二百亿", normalized)
        self.assertEqual(_numbers(normalized), ["2022", "2023"])

    def test_ignores_inverse_unit_exponents_in_numeric_validation(self) -> None:
        self.assertEqual(
            _numbers("Carbon losses were 2.3 kt C year−1 and 4 USD year−1."),
            ["2.3", "4"],
        )
        self.assertEqual(_numbers("Temperature fell to −5 degrees."), ["-5"])

    def test_attached_quantity_units_keep_the_source_number(self) -> None:
        self.assertEqual(
            _numbers("Aid raised furloughs by 24pp at a 1km2 scale."),
            ["24", "1"],
        )
        self.assertEqual(_numbers("The subsidy was about 606US$."), ["606"])

    def test_arabic_percentage_point_translation_matches_source_percent(self) -> None:
        source = "Welfare rises by 10 percent."
        translated = "福利提高10个百分点。"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertIn("10%", normalized)
        self.assertEqual(_numbers(normalized), ["10%"])

    def test_writes_translation_cache_with_provenance(self) -> None:
        issue = {"journal_id": "test", "articles": [ARTICLE]}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                cache_path = Path(directory) / "test.json"
                result = translate_missing(
                    issue,
                    cache_path,
                    token="test-token",
                    model="test/model",
                    session=FakeSession(),
                )
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["translated"], 1)
        self.assertEqual(cache[ARTICLE["doi"]]["title_cn"], "政策检验")
        self.assertEqual(
            cache[ARTICLE["doi"]]["translation"]["provider"],
            "github-models",
        )
        self.assertRegex(cache[ARTICLE["doi"]]["source_hash"], r"^[0-9a-f]{64}$")

    def test_request_translation_restores_protected_numbers(self) -> None:
        import json as _json

        class EchoResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": _json.dumps(
                                    {
                                        "title_cn": "政策检验",
                                        "abstract_cn": (
                                            "本文研究[[96]]项政策，发现排放下降[[12.5%]]，"
                                            "同时福利提高。估计过程完整保留了论文的"
                                            "研究设计、变量定义与结论方向，并忠实呈现"
                                            "原始摘要中的经验结果。"
                                        ),
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        class EchoSession:
            def post(self, url: str, *args, **kwargs) -> EchoResponse:
                return EchoResponse()

        result = request_translation(
            ARTICLE,
            token="test-token",
            protect_numbers=True,
            session=EchoSession(),
        )
        self.assertIn("96项政策", result["abstract_cn"])
        self.assertIn("12.5%", result["abstract_cn"])

    def test_prefers_deepseek_when_key_configured(self) -> None:
        issue = {"journal_id": "test", "articles": [ARTICLE]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "test-deepseek-key", "DEEPSEEK_MODEL": ""},
            clear=False,
        ):
            cache_path = Path(directory) / "test.json"
            result = translate_missing(
                issue,
                cache_path,
                token="",
                session=FakeSession(),
            )
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["translated"], 1)
        self.assertEqual(
            cache[ARTICLE["doi"]]["translation"]["provider"],
            "deepseek",
        )
        self.assertEqual(
            cache[ARTICLE["doi"]]["translation"]["model"],
            "deepseek-v4-flash",
        )

    def test_deepseek_retries_with_visible_numbers_after_placeholder_loss(self) -> None:
        class AdaptiveSession:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, *args, **kwargs) -> FakeResponse:
                self.calls += 1
                payload = kwargs["json"]
                source = payload["messages"][1]["content"]
                if "ADNUM" in source:
                    class MissingNumberResponse(FakeResponse):
                        def json(self) -> dict:
                            return {
                                "choices": [{"message": {"content": json.dumps({
                                    "title_cn": "政策检验",
                                    "abstract_cn": "本文研究相关政策，发现排放下降，同时福利提高。",
                                }, ensure_ascii=False)}}]
                            }

                    return MissingNumberResponse()
                return FakeResponse()

        session = AdaptiveSession()
        translated = request_deepseek_translation(
            ARTICLE,
            token="deepseek-token",
            session=session,
            retries=1,
        )
        self.assertEqual(session.calls, 2)
        self.assertIn("96", translated["abstract_cn"])
        self.assertIn("12.5%", translated["abstract_cn"])

    def test_retranslates_invalid_cached_entry(self) -> None:
        issue = {"journal_id": "test", "articles": [ARTICLE]}
        invalid_cache = {
            ARTICLE["doi"]: {
                "title_cn": "政策检验",
                "abstract_cn": "本文省略数字但保留了其余研究背景与经验结论。" * 5,
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "test.json"
            cache_path.write_text(
                json.dumps(invalid_cache, ensure_ascii=False),
                encoding="utf-8",
            )
            result = translate_missing(
                issue,
                cache_path,
                token="test-token",
                model="test/model",
                session=FakeSession(),
            )
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["invalid_cache_entries"], 1)
        self.assertEqual(result["translated"], 1)
        self.assertIn("96", cache[ARTICLE["doi"]]["abstract_cn"])

    def test_upgrades_valid_legacy_cache_without_retranslation(self) -> None:
        valid_translation = _extract_json(
            FakeResponse().json()["choices"][0]["message"]["content"]
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "test.json"
            cache_path.write_text(
                json.dumps(
                    {ARTICLE["doi"]: valid_translation},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = translate_missing(
                {"journal_id": "test", "articles": [ARTICLE]},
                cache_path,
                token="unused",
                session=ExplodingSession(),
            )
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["translated"], 0)
        self.assertEqual(result["invalid_cache_entries"], 0)
        self.assertEqual(result["upgraded_cache_entries"], 1)
        self.assertRegex(cache[ARTICLE["doi"]]["source_hash"], r"^[0-9a-f]{64}$")

    def test_falls_back_when_github_models_is_forbidden(self) -> None:
        issue = {"journal_id": "test", "articles": [ARTICLE]}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                cache_path = Path(directory) / "test.json"
                result = translate_missing(
                    issue,
                    cache_path,
                    token="forbidden-token",
                    session=FallbackSession(),
                )
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["translated"], 1)
        self.assertEqual(result["fallback_translated"], 1)
        self.assertEqual(
            cache[ARTICLE["doi"]]["translation"]["provider"],
            "google-translate",
        )

    def test_terminal_provider_failures_are_shared_across_issue_batch(self) -> None:
        second_article = {
            **ARTICLE,
            "doi": "10.0000/second-example",
            "title_en": "A Second Test of Policy",
        }
        provider_state: dict[str, str] = {}
        session = CircuitBreakingSession()
        with patch(
            "scripts.translate_issue.time.sleep"
        ), patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}, clear=False):
            with tempfile.TemporaryDirectory() as directory:
                first_result = translate_missing(
                    {"journal_id": "first", "articles": [ARTICLE]},
                    Path(directory) / "first.json",
                    token="expired-token",
                    session=session,
                    provider_state=provider_state,
                )
                second_result = translate_missing(
                    {"journal_id": "second", "articles": [second_article]},
                    Path(directory) / "second.json",
                    token="expired-token",
                    session=session,
                    provider_state=provider_state,
                )

        self.assertEqual(len(first_result["failed"]), 1)
        self.assertEqual(len(second_result["failed"]), 1)
        self.assertEqual(session.github_requests, 1)
        self.assertEqual(session.google_requests, 6)
        self.assertIn("github-models", provider_state)
        self.assertNotIn("google-translate", provider_state)
        self.assertEqual(second_result["provider_state"], provider_state)


    def test_restore_reinserts_space_when_google_fuses_placeholder_with_word(self) -> None:
        protected = "The [[2019]] MFP allocated funds."
        fused = "[[2019]]MFP"
        replacements = {"[[2019]]": "2019"}
        restored = _restore_numbers(fused, replacements)
        self.assertEqual("2019 MFP", restored)
        self.assertIn("2019", _numbers(restored))

    def test_google_letter_fusion_does_not_break_numeric_validation(self) -> None:
        article = {
            "title_en": "The political benefits of the monoculture",
            "abstract_en": (
                "In the 2019 wave of the Market Facilitation Program (MFP), "
                "the 2019 MFP allocated $14.5 billion via a formula. "
                "In the 2020 election, an additional $1 million raised the "
                "2020 two-party vote share by about .18 percentage points."
            ),
            "article_type": "research-article",
        }
        protected, replacements = _protect_numbers(article["abstract_en"])
        # Google fuses the placeholder with the following English word.
        fused = protected.replace("]] MFP", "]]MFP")
        restored = _restore_numbers(fused, replacements)
        self.assertEqual(
            _numbers(article["abstract_en"]),
            _numbers(restored),
        )
        validate_translation(
            article,
            {
                "title_cn": "单一作物制的政治收益",
                "abstract_cn": "中文测试：" + restored,
            },
        )


class GoogleMonthNumberTests(unittest.TestCase):
    """Google renders English months as Arabic digits; normalization must not
    flag that as an invented number (EER Brexit article regression)."""

    def test_google_month_digits_are_normalized_not_flagged(self) -> None:
        from scripts.translate_issue import (
            request_google_translation,
            validate_translation,
        )

        article = {
            "doi": "10.0000/brexit",
            "title_en": "Visual bias in the Brexit referendum",
            "abstract_en": (
                "We use 64,089 images collected between February and August "
                "2016. The 2016 referendum bias is concentrated during the "
                "campaign period."
            ),
        }

        class MonthSession:
            def post(self, url: str, *args, **kwargs) -> GoogleResponse:
                return GoogleResponse(
                    "英国脱欧公投中的视觉偏见\n[[9876543210123456789]]\n"
                    "我们使用2016年2月至8月期间收集的[[64,089]]张图像。"
                    "2016年公投偏见集中在竞选期间。"
                )

        translated = request_google_translation(
            article, session=MonthSession()
        )
        validate_translation(article, translated)
        self.assertIn("二月", translated["abstract_cn"])
        self.assertIn("八月", translated["abstract_cn"])


if __name__ == "__main__":
    unittest.main()
