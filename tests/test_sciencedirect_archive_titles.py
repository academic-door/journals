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


if __name__ == "__main__":
    unittest.main()
