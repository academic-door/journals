import unittest

from scripts.build_sciencedirect_browser_archives import metadata_title_for_compare


class ScienceDirectTitleNumericSuffixTests(unittest.TestCase):
    def test_preserves_legitimate_terminal_year(self):
        title = "A new reconstruction of cropland spatial distribution in China since 1850"
        self.assertEqual(metadata_title_for_compare(title), title)

    def test_still_strips_standalone_marker_before_metadata_note(self):
        title = (
            "Explaining the direction of emissions embodied in trade from hypotheses based on country rankings\n"
            "  1\n"
            "Funding information: Bingqian Yan received financial support."
        )
        self.assertEqual(
            metadata_title_for_compare(title),
            "Explaining the direction of emissions embodied in trade from hypotheses based on country rankings",
        )


if __name__ == "__main__":
    unittest.main()
