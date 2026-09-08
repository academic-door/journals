import unittest
from datetime import datetime, timezone

from collectors.metadata_fallback import _issue_is_not_future


def _year_only_items(year: int) -> list[dict]:
    return [{"published": {"date-parts": [[year]]}}]


class RandSeasonHorizonTests(unittest.TestCase):
    def test_fall_issue_is_current_in_september(self) -> None:
        self.assertTrue(
            _issue_is_not_future(
                "0741-6261",
                "57",
                "3",
                _year_only_items(2026),
                now=datetime(2026, 9, 8, tzinfo=timezone.utc),
            )
        )

    def test_winter_issue_remains_future_in_september(self) -> None:
        self.assertFalse(
            _issue_is_not_future(
                "0741-6261",
                "57",
                "4",
                _year_only_items(2026),
                now=datetime(2026, 9, 8, tzinfo=timezone.utc),
            )
        )
