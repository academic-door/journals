from __future__ import annotations

import unittest


class TranslationAnaphoricOneTests(unittest.TestCase):
    def test_parallel_environment_proform_is_not_a_numeric_quantity(self) -> None:
        from scripts.translate_issue import resolve_semantic_quantities, validate_translation

        article = {
            "article_type": "research-article",
            "title_en": "The limits of ex post implementation without transfers",
            "abstract_en": (
                "We study ex post implementation in collective decision problems where "
                "monetary transfers cannot be used. We find that deterministic ex post "
                "implementation is impossible if the underlying environment is neither "
                "almost an environment with private values nor almost one with common "
                "values. Thus, desirable properties of ex post implementation such as "
                "informational robustness become difficult to achieve when preference "
                "interdependence and preference heterogeneity are both present in the "
                "environment."
            ),
        }
        translated = {
            "title_cn": "无转移支付条件下事后实施的局限",
            "abstract_cn": (
                "我们研究无法使用货币转移支付的集体决策问题中的事后实施。"
                "我们发现，如果基础环境既不近似于私人价值环境，也不近似于共同"
                "价值环境，则确定性事后实施是不可能的。因此，当偏好相互依赖和"
                "偏好异质性同时存在时，事后实施所期望的信息稳健性等性质就难以实现。"
            ),
        }

        source_q, translated_q = resolve_semantic_quantities(
            article["abstract_en"], translated["abstract_cn"]
        )
        self.assertEqual(0, source_q["1"])
        self.assertEqual(0, translated_q["1"])
        validate_translation(article, translated)

    def test_unit_bearing_one_remains_a_required_numeric_quantity(self) -> None:
        from scripts.translate_issue import (
            TranslationError,
            resolve_semantic_quantities,
            validate_translation,
        )

        article = {
            "article_type": "research-article",
            "title_en": "A persistent treatment effect",
            "abstract_en": (
                "Under the limiting model, the policy effect persists for one year after "
                "treatment and remains stable across repeated simulations and alternative "
                "specifications."
            ),
        }
        translated_missing = {
            "title_cn": "持续的处理效应",
            "abstract_cn": (
                "在极限模型下，政策效应在处理后持续存在，并且在重复模拟和不同"
                "模型设定中保持稳定。"
            ),
        }
        translated_ok = {
            "title_cn": "持续的处理效应",
            "abstract_cn": (
                "在极限模型下，政策效应在处理后持续一年，并且在重复模拟和不同"
                "模型设定中保持稳定。"
            ),
        }

        source_q, missing_q = resolve_semantic_quantities(
            article["abstract_en"], translated_missing["abstract_cn"]
        )
        _, ok_q = resolve_semantic_quantities(
            article["abstract_en"], translated_ok["abstract_cn"]
        )
        self.assertEqual(1, source_q["1"])
        self.assertEqual(0, missing_q["1"])
        self.assertEqual(1, ok_q["1"])
        with self.assertRaises(TranslationError):
            validate_translation(article, translated_missing)
        validate_translation(article, translated_ok)

    def test_bare_one_without_parallel_noun_antecedent_remains_numeric(self) -> None:
        from scripts.translate_issue import resolve_semantic_quantities

        source_q, _ = resolve_semantic_quantities(
            "The normalized score is neither almost zero nor almost one with rounding applied.",
            "归一化得分在舍入后既不接近0，也不接近1。",
        )
        self.assertEqual(1, source_q["0"])
        self.assertEqual(1, source_q["1"])


if __name__ == "__main__":
    unittest.main()
