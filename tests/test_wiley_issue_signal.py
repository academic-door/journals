from __future__ import annotations

import unittest

from collectors.wiley import parse_latest_issue_signal


RAND_LANDING = b"""
<html><body>
  <section>
    <h2>Recent issues</h2>
    <ul>
      <li>
        <a href="/toc/17562171/2026/57/3">Volume 57, Issue 3</a>
        <p>Pages: 469-807</p>
        <p>Autumn (Fall) 2026</p>
      </li>
      <li>
        <a href="/toc/17562171/2026/57/2">Volume 57, Issue 2</a>
        <p>Summer 2026</p>
      </li>
    </ul>
  </section>
  <section>
    <h2>Most cited</h2>
    <a href="/toc/17562171/2025/56/4">Volume 56, Issue 4</a>
  </section>
</body></html>
"""


class WileyIssueSignalTests(unittest.TestCase):
    def test_recent_issues_returns_latest_official_issue_identity(self) -> None:
        signal = parse_latest_issue_signal(
            RAND_LANDING,
            "https://onlinelibrary.wiley.com/journal/17562171",
        )

        self.assertEqual("57", signal["volume"])
        self.assertEqual("3", signal["issue"])
        self.assertEqual("Autumn (Fall) 2026", signal["publication_date"])
        self.assertEqual("official_archive", signal["source_kind"])
        self.assertEqual(
            "https://onlinelibrary.wiley.com/toc/17562171/2026/57/3",
            signal["source_url"],
        )

    def test_missing_recent_issues_fails_closed(self) -> None:
        html = b"<html><body><a href='/toc/17562171/2026/57/3'>Volume 57, Issue 3</a></body></html>"
        with self.assertRaises(ValueError):
            parse_latest_issue_signal(
                html,
                "https://onlinelibrary.wiley.com/journal/17562171",
            )

    def test_non_official_source_url_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            parse_latest_issue_signal(
                RAND_LANDING,
                "https://example.com/journal/17562171",
            )


if __name__ == "__main__":
    unittest.main()
