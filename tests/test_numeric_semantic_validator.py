"""Semantic numeric canonicalization + release-audit wiring tests.

These tests prove three things:

1. ``_semantic_numbers`` canonicalizes numeric *quantities* (not surface
   tokens), so semantically equivalent renderings compare equal and scale
   errors are still detected (fail closed, no allowlist).
2. ``validate_translation`` (the gate the public-data audit runs) actually
   consults the semantic canonicalizer.
3. ``audit_public_data`` imports the *same* ``validate_translation``, so the
   release audit cannot run with a stale/bare-surface numeric checker.
"""
import unittest
from unittest import mock

from scripts.translate_issue import (
    TranslationError,
    _semantic_numbers,
    validate_translation,
)

_PAD = (
    "本文考察该政策对经济结果的影响机制，并结合跨国数据与既有文献展开讨论。"
    "研究结果揭示了一系列值得深入检验的经济学含义。"
)


def _article(source_text: str) -> dict:
    return {
        "article_type": "research-article",
        "title_en": "Policy effects",
        "abstract_en": source_text,
    }


def _translation(translated_text: str) -> dict:
    return {
        "title_cn": "测试标题",
        "abstract_cn": _PAD + translated_text,
    }


class SemanticNumberCanonicalizationTests(unittest.TestCase):
    EQUIVALENT = [
        ("100 million", "100000000"),
        ("40 million", "40000000"),
        ("83 thousand", "83000"),
        ("13 million", "1300万"),
        ("two decades", "20年"),
        ("six decades", "60年"),
        # English written + scale, currency, Chinese scale rendering.
        ("a million", "1000000"),
        ("$3.45 million", "3450000"),
        ("83 千美元", "83000美元"),
        ("1.0 百万美元", "100万美元"),
        ("200亿", "20000000000"),
        # Unicode hyphen/minus normalization.
        ("-1.0", "−1.0"),
    ]

    INEQUIVALENT = [
        ("$1.0 million", "1.0万美元"),
        ("40 million", "40万"),
        ("13 million", "130万"),
        ("six decades", "50年"),
        ("$3.45 million", "3.45"),
        ("83 thousand", "83万"),
    ]

    def test_semantic_canonicalizes_equivalent_renderings(self) -> None:
        for source, translated in self.EQUIVALENT:
            with self.subTest(source=source, translated=translated):
                self.assertEqual(
                    _semantic_numbers(source),
                    _semantic_numbers(translated),
                    f"{source!r} and {translated!r} should be semantically equal",
                )

    def test_semantic_canonicalizer_rejects_scale_errors(self) -> None:
        for source, translated in self.INEQUIVALENT:
            with self.subTest(source=source, translated=translated):
                self.assertNotEqual(
                    _semantic_numbers(source),
                    _semantic_numbers(translated),
                    f"{source!r} and {translated!r} must differ semantically",
                )

    def test_semantic_preserves_percent_distinction(self) -> None:
        self.assertNotEqual(_semantic_numbers("100%"), _semantic_numbers("100"))



class SemanticNumberValidationTests(unittest.TestCase):
    def test_valid_equivalences_pass_validation(self) -> None:
        cases = [
            ("100 million", "100000000"),
            ("40 million", "40000000"),
            ("83 thousand", "83000"),
            ("13 million", "1300万"),
            ("two decades", "20年"),
            ("six decades", "60年"),
            ("$3.45 million", "345万美元"),
        ]
        for source, translated in cases:
            with self.subTest(source=source, translated=translated):
                validate_translation(_article(source), _translation(translated))

    def test_invalid_scale_errors_fail_validation(self) -> None:
        cases = [
            ("$1.0 million", "1.0万美元"),
            ("40 million", "40万"),
            ("13 million", "130万"),
            ("six decades", "50年"),
            ("$3.45 million", "$3.45至$百万美元"),
        ]
        for source, translated in cases:
            with self.subTest(source=source, translated=translated):
                with self.assertRaises(TranslationError):
                    validate_translation(_article(source), _translation(translated))


class AuditPublicDataSemanticWiringTests(unittest.TestCase):
    """Prove the release audit gate goes through the semantic validator."""

    def test_audit_imports_the_same_validate_translation(self) -> None:
        import scripts.audit_public_data as audit
        import scripts.translate_issue as ti

        self.assertIs(audit.validate_translation, ti.validate_translation)

    def test_validate_translation_consults_semantic_canonicalizer(self) -> None:
        import scripts.translate_issue as ti

        source = "aggregated welfare benefits of $3.45 million annually"
        translated = "每年总计345万美元的福利总收益"
        with mock.patch.object(
            ti, "_semantic_numbers", wraps=ti._semantic_numbers
        ) as spy:
            validate_translation(
                _article(source),
                _translation(translated),
            )
        self.assertGreater(spy.call_count, 0)




class SemanticHardeningTests(unittest.TestCase):
    """A1/A2/A3 hardening regression tests for validator defects."""

    # --- A1: exact decimal canonicalization (no binary-float residue) ---
    def test_a1_decimal_scale_is_exact(self) -> None:
        self.assertEqual(_semantic_numbers("1.033 billion"), ["1033000000"])
        self.assertEqual(_semantic_numbers("17.81 billion"), ["17810000000"])
        self.assertEqual(_semantic_numbers("3.45 million"), ["3450000"])
        self.assertEqual(_semantic_numbers("1033000000"), ["1033000000"])

    def test_a1_no_binary_float_residue(self) -> None:
        for text in ("1.033 billion", "17.81 billion", "3.45 million"):
            for token in _semantic_numbers(text):
                self.assertNotIn(".", token, f"{text!r} produced a float token {token!r}")

    def test_a1_decimal_non_integer_is_stable(self) -> None:
        self.assertEqual(_semantic_numbers("1.033"), ["1.033"])
        self.assertEqual(_semantic_numbers("3.45"), ["3.45"])

    # --- A2: source-aware single Chinese digit reconciliation ---
    def test_a2_single_chinese_digit_matches_source_quantity(self) -> None:
        validate_translation(
            _article("3 years"), _translation("三年")
        )
        validate_translation(
            _article("5 groups"), _translation("五组")
        )
        # A single digit is confirmed when the source carries the exact value.
        validate_translation(_article("3 years"), _translation("三"))

    def test_a2_single_chinese_digit_is_fail_closed(self) -> None:
        # A different quantity (source 3 years vs 2 months) must fail.
        with self.assertRaises(TranslationError):
            validate_translation(
                _article("3 years"), _translation("两个月")
            )
        # A non-quantified count word is not fabricated into a number.
        validate_translation(_article("two types"), _translation("两种"))
        # A standalone "三" with no source quantity does not invent a 3.
        validate_translation(_article("several"), _translation("三"))
        # A precise Chinese numeral absent from the source is still flagged.
        with self.assertRaises(TranslationError):
            validate_translation(_article("several"), _translation("三千"))

    # --- A3: year/scale adjacency, identifier boundaries, percent ---
    def test_a3_year_is_not_scaled(self) -> None:
        self.assertEqual(_semantic_numbers("in the 1970s"), ["1970"])
        self.assertEqual(_semantic_numbers("2023年"), ["2023"])
        self.assertEqual(_semantic_numbers("from 1970 to 2010"), ["1970", "2010"])
        # An Arabic "year" adjacent to a scale unit with no space means a quantity
        # (2023万 = 20,230,000), not a year; that is a deliberate scalar.
        self.assertEqual(_semantic_numbers("2023万"), ["20230000"])

    def test_a3_identifier_ordinals_are_not_quantities(self) -> None:
        self.assertEqual(_semantic_numbers("Section 5503"), [])
        self.assertEqual(_semantic_numbers("Table 10"), [])
        self.assertEqual(_semantic_numbers("Study 2"), [])
        self.assertEqual(_semantic_numbers("第2轮"), [])
        self.assertEqual(_semantic_numbers("研究三"), [])

    def test_a3_percent_distinct_from_bare(self) -> None:
        self.assertNotEqual(_semantic_numbers("100%"), _semantic_numbers("100"))

    def test_a3_unicode_punctuation_no_span_overlap(self) -> None:
        self.assertEqual(_semantic_numbers("3 - 5"), ["3", "5"])
        self.assertEqual(_semantic_numbers("3.45–4.50"), ["3.45", "4.50"])
        self.assertEqual(_semantic_numbers("2,000万人"), ["20000000"])


if __name__ == "__main__":
    unittest.main()
