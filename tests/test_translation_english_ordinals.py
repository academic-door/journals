from __future__ import annotations

import unittest


class EnglishOrdinalSemanticQuantityTests(unittest.TestCase):
    def test_thirtieth_year_matches_chinese_arabic_ordinal(self) -> None:
        from scripts.translate_issue import resolve_semantic_quantities

        source, translated = resolve_semantic_quantities(
            "Adoption accelerates after the tenth year and differs by the thirtieth year (24% of farms).",
            "采用率在第10年后加速，并在第30年呈现差异（24%的农场）。",
        )

        self.assertEqual(source, translated)
        self.assertEqual(1, source["10"])
        self.assertEqual(1, source["30"])
        self.assertEqual(1, source["24%"])

    def test_ordinal_fix_does_not_hide_real_added_number(self) -> None:
        from scripts.translate_issue import resolve_semantic_quantities

        source, translated = resolve_semantic_quantities(
            "Adoption differs by the thirtieth year (24% of farms).",
            "采用率在第30年呈现差异（24%的农场，额外增加5）。",
        )

        self.assertEqual(0, source["5"])
        self.assertEqual(1, translated["5"])


if __name__ == "__main__":
    unittest.main()
