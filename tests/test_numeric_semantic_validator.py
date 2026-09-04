"""Shadow semantic numeric contract tests.

Stage A keeps the production ``validate_translation`` release gate unchanged
(surface-token ``_numbers``) while exposing the semantic numeric engine as an
independent, non-gating shadow capability.

These tests lock:
1. semantic canonicalization (``_semantic_numbers``) and the source-aware
   resolver (``resolve_semantic_quantities``) behave as expected;
2. the production ``validate_translation`` release authority is UNCHANGED and
   does not consult the semantic engine;
3. ``audit_public_data`` still imports the production ``validate_translation``;
4. the shadow audit sees debt but NEVER changes the release result (exit 0 on
   findings).
"""
import unittest
from unittest import mock

from scripts.translate_issue import (
    TranslationError,
    _semantic_numbers,
    resolve_semantic_quantities,
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
        ("a million", "1000000"),
        ("$3.45 million", "3450000"),
        ("83 千美元", "83000美元"),
        ("1.0 百万美元", "100万美元"),
        ("200亿", "20000000000"),
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


class SemanticResolverTests(unittest.TestCase):
    """The source-aware semantic path used by the shadow audit."""

    def test_resolver_sees_equivalences(self) -> None:
        for source, translated in [
            ("100 million", "100000000"),
            ("40 million", "40000000"),
            ("83 thousand", "83000"),
            ("13 million", "1300万"),
            ("two decades", "20年"),
            ("six decades", "60年"),
            ("$3.45 million", "345万美元"),
        ]:
            with self.subTest(source=source, translated=translated):
                sq, tq = resolve_semantic_quantities(source, translated)
                self.assertEqual(sq, tq, f"{source!r} <-> {translated!r}")

    def test_resolver_sees_scale_corruption(self) -> None:
        for source, translated in [
            ("$1.0 million", "1.0万美元"),
            ("40 million", "40万"),
            ("13 million", "130万"),
            ("six decades", "50年"),
            ("$3.45 million", "$3.45至$百万美元"),
        ]:
            with self.subTest(source=source, translated=translated):
                sq, tq = resolve_semantic_quantities(source, translated)
                self.assertNotEqual(sq, tq, f"{source!r} <-> {translated!r}")


class ProductionGateUnchangedTests(unittest.TestCase):
    """Lock that the production release gate is the surface-token validator."""

    def test_production_validate_translation_is_surface_token_gate(self) -> None:
        # Under production semantics "100 million" (token "100") != "100000000".
        with self.assertRaises(TranslationError):
            validate_translation(
                _article("100 million"), _translation("100000000")
            )
        # ...whereas the shadow semantic resolver treats them as equal.
        sq, tq = resolve_semantic_quantities("100 million", "100000000")
        self.assertEqual(sq, tq)

    def test_production_validate_translation_does_not_use_semantic_engine(self) -> None:
        import scripts.translate_issue as ti

        # If production called _semantic_numbers this raises; a pass proves it does
        # not consult the semantic engine.
        with mock.patch.object(
            ti, "_semantic_numbers", side_effect=AssertionError(
                "production validate_translation must not use the semantic engine"
            )
        ):
            # "two decades" (no Arabic) vs "20年" (Arabic 20) mismatches under
            # surface-token production, exactly as it did before Stage A.
            with self.assertRaises(TranslationError):
                validate_translation(_article("two decades"), _translation("20年"))


class AuditPublicDataWiringTests(unittest.TestCase):
    def test_audit_imports_the_production_validate_translation(self) -> None:
        import scripts.audit_public_data as audit
        import scripts.translate_issue as ti

        self.assertIs(audit.validate_translation, ti.validate_translation)

    def test_shadow_audit_is_non_gating(self) -> None:
        # Shadow audit returns 0 even when the corpus has semantic mismatches.
        from scripts.audit_numeric_semantics_shadow import audit

        self.assertEqual(audit(), 0)

    def test_shadow_audit_sees_known_corruption(self) -> None:
        sq, tq = resolve_semantic_quantities(
            "$3.45 million annually", "$3.45至$百万美元"
        )
        self.assertNotEqual(sq, tq)


class SemanticHardeningTests(unittest.TestCase):
    """Parser-hardening regression tests."""

    def test_a1_decimal_scale_is_exact(self) -> None:
        self.assertEqual(_semantic_numbers("1.033 billion"), ["1033000000"])
        self.assertEqual(_semantic_numbers("17.81 billion"), ["17810000000"])
        self.assertEqual(_semantic_numbers("3.45 million"), ["3450000"])

    def test_a1_no_binary_float_residue(self) -> None:
        for text in ("1.033 billion", "17.81 billion", "3.45 million"):
            for token in _semantic_numbers(text):
                self.assertNotIn(".", token)

    def test_a2_single_chinese_digit_matches_source_quantity(self) -> None:
        sq, tq = resolve_semantic_quantities("3 years", "三年")
        self.assertEqual(sq, tq)
        sq, tq = resolve_semantic_quantities("5 groups", "五组")
        self.assertEqual(sq, tq)

    def test_a2_single_chinese_digit_is_fail_closed(self) -> None:
        sq, tq = resolve_semantic_quantities("3 years", "两个月")
        self.assertNotEqual(sq, tq)
        # A count word does not fabricate a number.
        sq, tq = resolve_semantic_quantities("two types", "两种")
        self.assertEqual(sq, tq)
        # A lone "三" without a source quantity does not invent a 3.
        sq, tq = resolve_semantic_quantities("several", "三")
        self.assertEqual(sq, tq)

    def test_a3_year_is_not_scaled(self) -> None:
        self.assertEqual(_semantic_numbers("in the 1970s"), ["1970"])
        self.assertEqual(_semantic_numbers("2023年"), ["2023"])
        self.assertEqual(_semantic_numbers("from 1970 to 2010"), ["1970", "2010"])
        self.assertEqual(_semantic_numbers("2023万"), ["20230000"])

    def test_a3_identifier_ordinals_are_not_quantities(self) -> None:
        self.assertEqual(_semantic_numbers("Section 5503"), [])
        self.assertEqual(_semantic_numbers("Table 10"), [])
        self.assertEqual(_semantic_numbers("Study 2"), [])
        self.assertEqual(_semantic_numbers("第2轮"), [])
        self.assertEqual(_semantic_numbers("研究三"), [])

    def test_a3_parser_classes(self) -> None:
        # Century and fraction descriptors are not quantities.
        self.assertEqual(_semantic_numbers("nineteenth century"), [])
        self.assertEqual(_semantic_numbers("二十世纪"), [])
        self.assertEqual(_semantic_numbers("three-quarters"), [])
        # Compound / composite cardinals and metric units are quantities.
        self.assertEqual(_semantic_numbers("fifteen"), ["15"])
        self.assertEqual(_semantic_numbers("thirty-five"), ["35"])
        self.assertEqual(_semantic_numbers("one hundred"), ["100"])
        self.assertEqual(_semantic_numbers("3.5 kt"), ["3500"])
        self.assertEqual(_semantic_numbers("近一百万"), ["1000000"])
        self.assertEqual(_semantic_numbers("数百万"), [])
        # "two centuries" keeps its count, matching "两个世纪" (resolver).
        sq, tq = resolve_semantic_quantities("two centuries", "两个世纪")
        self.assertEqual(sq, tq)

    def test_a3_percent_distinct_from_bare(self) -> None:
        self.assertNotEqual(_semantic_numbers("100%"), _semantic_numbers("100"))

    def test_a3_unicode_punctuation_no_span_overlap(self) -> None:
        self.assertEqual(_semantic_numbers("3 - 5"), ["3", "5"])
        self.assertEqual(_semantic_numbers("3.45–4.50"), ["3.45", "4.50"])
        self.assertEqual(_semantic_numbers("2,000万人"), ["20000000"])


if __name__ == "__main__":
    unittest.main()
