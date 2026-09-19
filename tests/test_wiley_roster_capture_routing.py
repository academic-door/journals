from __future__ import annotations

import unittest

from scripts.capture_wiley_roster_evidence import _url


class WileyRosterCaptureRoutingTests(unittest.TestCase):
    def test_manifest_official_url_wins_over_config_issn_reconstruction(self) -> None:
        selected = _url(
            {"issn": "1555-7561"},
            {
                "year": 2026,
                "volume": "21",
                "issue": "2",
                "official_url": (
                    "https://onlinelibrary.wiley.com/toc/15557561/2026/21/2"
                ),
            },
        )
        self.assertEqual(
            "https://onlinelibrary.wiley.com/toc/15557561/2026/21/2",
            selected,
        )

    def test_stale_manifest_route_is_rebuilt_from_current_config(self) -> None:
        selected = _url(
            {"issn": "1555-7561"},
            {
                "year": 2025,
                "volume": "20",
                "issue": "1",
                "official_url": "https://onlinelibrary.wiley.com/toc/15567568/20/1",
            },
        )
        self.assertEqual(
            "https://onlinelibrary.wiley.com/toc/15557561/2025/20/1",
            selected,
        )

    def test_legacy_record_without_route_keeps_existing_fallback_shape(self) -> None:
        selected = _url(
            {"issn": "15406261"},
            {"year": 2026, "volume": "81", "issue": "4"},
        )
        self.assertEqual(
            "https://onlinelibrary.wiley.com/toc/15406261/2026/81/4",
            selected,
        )


if __name__ == "__main__":
    unittest.main()
