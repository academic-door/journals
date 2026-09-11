from __future__ import annotations

import unittest
from collections import Counter

from scripts.translate_issue import resolve_semantic_quantities


class TranslationUnitySemanticTests(unittest.TestCase):
    def test_mathematical_unity_matches_numeric_one(self) -> None:
        source = (
            "Under homotheticity, the REMV always equals one, so saving is signed "
            "by the relationship of the EIS with unity."
        )
        translated = "在齐次性下，REMV始终等于1，因此储蓄取决于EIS与1的关系。"

        source_q, translated_q = resolve_semantic_quantities(source, translated)

        self.assertEqual(source_q, Counter({"1": 2}))
        self.assertEqual(translated_q, Counter({"1": 2}))

    def test_plain_language_unity_is_not_a_numeric_quantity(self) -> None:
        source_q, translated_q = resolve_semantic_quantities(
            "National unity strengthens social cohesion.",
            "民族团结加强社会凝聚力。",
        )

        self.assertEqual(source_q, Counter())
        self.assertEqual(translated_q, Counter())


if __name__ == "__main__":
    unittest.main()
