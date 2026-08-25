import unittest

from scripts.build_sciencedirect_browser_archives import (
    comparable_title,
    metadata_title_for_compare,
)


class ScienceDirectArchiveTitleTests(unittest.TestCase):
    def test_strips_numeric_marker_before_funding_note(self):
        api_title = (
            "Explaining the direction of emissions embodied in trade from "
            "hypotheses based on country rankings\n  1\n"
            "Funding information: Bingqian Yan received financial support."
        )
        roster_title = (
            "Explaining the direction of emissions embodied in trade from "
            "hypotheses based on country rankings"
        )
        self.assertEqual(
            comparable_title(metadata_title_for_compare(api_title)),
            comparable_title(roster_title),
        )

    def test_normalizes_hidden_unicode_presentation_characters(self):
        roster_title = "Insurer hedging amidst the interplay of black and green swans toward SDGs 3 and 7"
        api_title = "Insurer hedging amidst the interplay of black and green swans toward S\u200bDGs 3 and 7"
        self.assertEqual(comparable_title(roster_title), comparable_title(api_title))


if __name__ == "__main__":
    unittest.main()
