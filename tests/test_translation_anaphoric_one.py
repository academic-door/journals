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

    def test_true_almost_one_quantity_still_requires_one(self) -> None:
        from scripts.translate_issue import TranslationError, validate_translation

        article = {
            "article_type": "research-article",
            "title_en": "A probability limit",
            "abstract_en": (
                "The normalized probability is almost one under the limiting model, "
                "and the estimate remains stable across repeated simulations and "
                "alternative specifications."
            ),
        }
        translated_missing = {
            "title_cn": "概率极限",
            "abstract_cn": (
                "在极限模型下，归一化概率已接近其上界，而且该估计在重复模拟和"
                "不同模型设定中保持稳定。"
            ),
        }
        with self.assertRaises(TranslationError):
            validate_translation(article, translated_missing)

        translated_ok = {
            "title_cn": "概率极限",
            "abstract_cn": (
                "在极限模型下，归一化概率接近1，而且该估计在重复模拟和不同模型"
                "设定中保持稳定。"
            ),
        }
        validate_translation(article, translated_ok)


if __name__ == "__main__":
    unittest.main()
