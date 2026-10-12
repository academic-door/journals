"""Strict unit regressions for journals#403 / Draft PR #417.

WD source phrases are documented in #403, comment 6110598859:
10.1016/j.worlddev.2026.107552 (AQI index points) and
10.1016/j.worlddev.2026.107541 (an additional percentage point).
The inherited-rate phrase is from the official AEA abstract for
10.1257/pol.20240361. These are bounded reproducers, not full abstracts.
"""

from collections import Counter
import json
from pathlib import Path
import unittest

from scripts.translate_issue import (
    TranslationError,
    _semantic_numbers,
    resolve_semantic_quantities,
    validate_translation,
)


AQI_SOURCE = "The Air Quality Index (AQI) gap is 0.772 points."
INCREMENT_SOURCE = "An additional percentage point in the mayoral winning margin."
RATE_SOURCE = (
    "14 percentage points (pp), the likelihood of heavy drinking by 19 points"
)
PAD = "本文考察相关政策的作用机制，并忠实保留原文报告的研究变量和经验结果。"


def article(source: str) -> dict:
    return {"title_en": "Policy effects", "abstract_en": source}


def translation(text: str) -> dict:
    return {"title_cn": "政策影响", "abstract_cn": PAD + text}


class PercentagePointSemanticsTests(unittest.TestCase):
    def assert_valid(self, source: str, translated: str) -> None:
        source_q, translated_q = resolve_semantic_quantities(source, translated)
        self.assertEqual(source_q, translated_q)
        validate_translation(article(source), translation(translated))

    def assert_invalid(self, source: str, translated: str) -> None:
        source_q, translated_q = resolve_semantic_quantities(source, translated)
        self.assertNotEqual(source_q, translated_q)
        with self.assertRaisesRegex(TranslationError, "semantic numeric quantities"):
            validate_translation(article(source), translation(translated))

    def test_wd_aqi_index_points_are_preserved(self) -> None:
        self.assertEqual(_semantic_numbers(AQI_SOURCE), ["0.772"])
        self.assert_valid(AQI_SOURCE, "空气质量指数（AQI）的差距为0.772个指数点。")

    def test_wd_aqi_percent_and_percentage_points_are_rejected(self) -> None:
        for rendered in ("0.772%", "0.772个百分点"):
            with self.subTest(rendered=rendered):
                self.assert_invalid(AQI_SOURCE, f"空气质量指数（AQI）的差距为{rendered}。")

    def test_wd_aqi_missing_or_changed_value_is_rejected(self) -> None:
        for rendered in ("", "0.773个指数点"):
            with self.subTest(rendered=rendered):
                self.assert_invalid(AQI_SOURCE, f"空气质量指数（AQI）的差距为{rendered}。")

    def test_bare_index_point_range_does_not_acquire_percent_units(self) -> None:
        source = "The AQI gap is 0.772 to 0.9 points."
        self.assertEqual(_semantic_numbers(source), ["0.772", "0.9"])
        self.assert_valid(source, "空气质量指数差距为0.772至0.9个指数点。")
        self.assert_invalid(source, "空气质量指数差距为0.772至0.9个百分点。")

    def test_wd_additional_unit_allows_one_explicit_increment(self) -> None:
        for rendered in ("每增加1个百分点", "每增加一个百分点"):
            with self.subTest(rendered=rendered):
                self.assert_valid(INCREMENT_SOURCE, f"市长胜选差距{rendered}。")

    def test_wd_additional_unit_rejects_wrong_increment(self) -> None:
        for rendered in ("每增加2个百分点", "每增加0.5个百分点", "每增加1个点"):
            with self.subTest(rendered=rendered):
                self.assert_invalid(INCREMENT_SOURCE, f"市长胜选差距{rendered}。")

    def test_wd_additional_unit_rejects_second_invented_one(self) -> None:
        for extra in ("结果额外提高1%。", "另一指标每增加1个百分点。"):
            with self.subTest(extra=extra):
                self.assert_invalid(INCREMENT_SOURCE, "市长胜选差距每增加1个百分点。" + extra)

    def test_explicit_one_requires_source_backed_singular_unit(self) -> None:
        for source in (
            "An additional point in the mayoral winning margin.",
            "An additional percentage points estimate.",
            "The percentage point estimate of the mayoral winning margin.",
        ):
            with self.subTest(source=source):
                self.assert_invalid(source, "市长胜选差距每增加1个百分点。")

    def test_existing_each_and_every_unit_reconciliation_is_preserved(self) -> None:
        for quantifier in ("each", "every"):
            with self.subTest(quantifier=quantifier):
                source = f"For {quantifier} percentage point in the winning margin."
                self.assert_valid(source, "胜选差距每增加1个百分点。")
                self.assert_invalid(source, "胜选差距每增加2个百分点。")

    def test_aejpol_inherited_rate_points_are_preserved(self) -> None:
        self.assertEqual(Counter(_semantic_numbers(RATE_SOURCE)), Counter({"14%": 1, "19%": 1}))
        self.assert_valid(RATE_SOURCE, "相关概率降低14个百分点，酗酒的可能性降低19个百分点。")

    def test_aejpol_dropped_percent_unit_is_rejected(self) -> None:
        self.assert_invalid(RATE_SOURCE, "相关概率降低14个百分点，酗酒的可能性降低19个点。")

    def test_aejpol_missing_changed_or_duplicate_value_is_rejected(self) -> None:
        for outcome in ("", "18个百分点", "19个百分点，另一概率降低19个百分点"):
            with self.subTest(outcome=outcome):
                self.assert_invalid(RATE_SOURCE, f"相关概率降低14个百分点，酗酒的可能性降低{outcome}。")

    def test_bare_rate_points_require_prior_explicit_percent_unit(self) -> None:
        source = "The likelihood of heavy drinking decreased by 19 points."
        self.assertEqual(_semantic_numbers(source), ["19"])
        self.assert_invalid(source, "酗酒的可能性降低19个百分点。")

    def test_percent_unit_is_not_inherited_across_sentences(self) -> None:
        for separator in (". ", "! ", "? ", "\n"):
            with self.subTest(separator=separator):
                source = "The likelihood fell by 14 percentage points" + separator + "The AQI rate index fell by 19 points."
                self.assertEqual(Counter(_semantic_numbers(source)), Counter({"14%": 1, "19": 1}))
                self.assert_valid(source, "相关概率降低14个百分点，AQI指数降低19个指数点。")
                self.assert_invalid(source, "相关概率降低14个百分点，AQI指数降低19个百分点。")

    def test_percent_unit_does_not_spread_to_index_outcome_in_same_sentence(self) -> None:
        source = "The likelihood fell by 14 percentage points, while the AQI rate index fell by 19 points."
        self.assertEqual(Counter(_semantic_numbers(source)), Counter({"14%": 1, "19": 1}))
        self.assert_valid(source, "相关概率降低14个百分点，而AQI指数降低19个指数点。")
        self.assert_invalid(source, "相关概率降低14个百分点，而AQI指数降低19个百分点。")

    def test_aejpol_published_translation_remains_valid(self) -> None:
        path = Path(__file__).resolve().parents[1] / "public/api/v1/journals/aejpol/issues/current.json"
        issue = json.loads(path.read_text(encoding="utf-8"))
        published = next(a for a in issue["articles"] if a["doi"] == "10.1257/pol.20240361")
        validate_translation(published, published)
        source_q, translated_q = resolve_semantic_quantities(
            published["title_en"] + "\n" + published["abstract_en"],
            published["title_cn"] + "\n" + published["abstract_cn"],
        )
        self.assertEqual(source_q, Counter({"16": 1, "14%": 1, "19%": 1, "32%": 1}))
        self.assertEqual(source_q, translated_q)


if __name__ == "__main__":
    unittest.main()
