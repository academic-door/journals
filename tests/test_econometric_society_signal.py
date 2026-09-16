from __future__ import annotations

import unittest

from collectors.econometric_society import parse_latest_econometrica_issue_signal


OFFICIAL_2026_PAGE = b"""
<html><body>
  <main>
    <h1>Econometrica - Volume 94</h1>
    <section><h2>Issue 4 (July 2026)</h2></section>
    <section><h2>Issue 5 (September 2026)</h2></section>
  </main>
</body></html>
"""


class EconometricSocietySignalTests(unittest.TestCase):
    def test_latest_issue_heading_returns_first_party_identity(self) -> None:
        signal = parse_latest_econometrica_issue_signal(
            OFFICIAL_2026_PAGE,
            "https://www.econometricsociety.org/publications/econometrica/volume/2026",
        )

        self.assertEqual("94", signal["volume"])
        self.assertEqual("5", signal["issue"])
        self.assertEqual("September 2026", signal["publication_date"])
        self.assertEqual("association_announcement", signal["source_kind"])
        self.assertEqual(
            "https://www.econometricsociety.org/publications/econometrica/volume/2026",
            signal["source_url"],
        )

    def test_non_official_host_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            parse_latest_econometrica_issue_signal(
                OFFICIAL_2026_PAGE,
                "https://example.com/publications/econometrica/volume/2026",
            )

    def test_missing_volume_or_issue_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            parse_latest_econometrica_issue_signal(
                b"<html><body><h2>Issue 5 (September 2026)</h2></body></html>",
                "https://www.econometricsociety.org/publications/econometrica/volume/2026",
            )


if __name__ == "__main__":
    unittest.main()
