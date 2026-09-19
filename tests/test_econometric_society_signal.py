from __future__ import annotations

import unittest

from collectors.econometric_society import (
    parse_latest_econometrica_issue_signal,
    parse_latest_econometrica_roster,
    qualify_crossref_issue_with_econometric_society,
)


OFFICIAL_2026_PAGE = b"""
<html><body>
  <main>
    <h1>Econometrica - Volume 94</h1>
    <section><h2>Issue 4 (July 2026)</h2></section>
    <section><h2>Issue 5 (September 2026)</h2></section>
  </main>
</body></html>
"""


OFFICIAL_ROSTER_PAGE = b"""
<html><body>
  <main>
    <h1>Econometrica - Volume 94</h1>
    <h2>Issue 4 (July 2026)</h2>
    <a href="/publications/econometrica/2026/07/01/Old-Paper">Old Paper</a>
    <a href="/member-authentication/wb?doi=10.3982%2FECTA00001">Access</a>
    <h2>Issue 5 (September 2026)</h2>
    <a href="/publications/econometrica/2026/09/01/Frontmatter-of-Econometrica-94-Iss-5">Frontmatter of Econometrica 94 Iss 5</a>
    <a href="/member-authentication/wb?doi=10.3982%2FECTA945FM">Access</a>
    <a href="/publications/econometrica/2026/09/01/First-Research-Paper">First Research Paper</a>
    <a href="/member-authentication/wb?doi=10.3982%2FECTA11111">Access</a>
    <a href="/publications/econometrica/2026/09/01/First-Research-Paper#supplemental_material">Supplement</a>
    <a href="/publications/econometrica/2026/09/01/Second-Research-Paper">Second Research Paper</a>
    <a href="/member-authentication/wb?doi=10.3982%2FECTA22222">Access</a>
    <a href="/publications/econometrica/2026/09/01/Backmatter-of-Econometrica-Vol-94-Iss-5">Backmatter of Econometrica Vol 94 Iss 5</a>
    <a href="/member-authentication/wb?doi=10.3982%2FECTA945BM">Access</a>
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


    def test_latest_roster_is_exact_ordered_first_party_evidence(self) -> None:
        roster = parse_latest_econometrica_roster(
            OFFICIAL_ROSTER_PAGE,
            "https://www.econometricsociety.org/publications/econometrica/volume/2026",
        )
        self.assertEqual("94", roster["volume"])
        self.assertEqual("5", roster["issue"])
        self.assertEqual("official_issue_page", roster["source_kind"])
        self.assertEqual(
            ["10.3982/ecta11111", "10.3982/ecta22222"],
            [item["doi"] for item in roster["articles"]],
        )
        self.assertEqual([1, 2], [item["sequence"] for item in roster["articles"]])
        self.assertEqual(2, roster["research_article_count"])
        self.assertEqual(2, len(roster["excluded_items"]))

    def test_crossref_candidate_is_promoted_only_on_exact_official_doi_set(self) -> None:
        roster = parse_latest_econometrica_roster(
            OFFICIAL_ROSTER_PAGE,
            "https://www.econometricsociety.org/publications/econometrica/volume/2026",
        )
        candidate = {
            "issue_id": "ecta-94-5",
            "journal_id": "ecta",
            "volume": "94",
            "issue": "5",
            "publication_date": "2026",
            "source_url": "https://onlinelibrary.wiley.com/toc/14680262/current",
            "expected_article_count": 2,
            "research_article_count": 2,
            "articles": [
                {"doi": "10.3982/ecta22222", "sequence": 1, "sources": {}},
                {"doi": "10.3982/ecta11111", "sequence": 2, "sources": {}},
            ],
            "quality": {
                "flags": [
                    "publisher_html_blocked_crossref_fallback",
                    "crossref_provisional_roster",
                    "translation_incomplete",
                ],
                "roster_match": True,
                "order_preserved": True,
            },
        }
        qualified = qualify_crossref_issue_with_econometric_society(candidate, roster)
        self.assertEqual(
            ["10.3982/ecta11111", "10.3982/ecta22222"],
            [item["doi"] for item in qualified["articles"]],
        )
        self.assertEqual("official-issue-page", qualified["quality"]["roster_authority"])
        self.assertEqual(
            "econometric-society-page", qualified["quality"]["roster_transport"]
        )
        self.assertNotIn(
            "crossref_provisional_roster", qualified["quality"]["flags"]
        )

    def test_roster_mismatch_fails_closed(self) -> None:
        roster = parse_latest_econometrica_roster(
            OFFICIAL_ROSTER_PAGE,
            "https://www.econometricsociety.org/publications/econometrica/volume/2026",
        )
        candidate = {
            "volume": "94",
            "issue": "5",
            "articles": [{"doi": "10.3982/ecta11111", "sources": {}}],
            "quality": {"flags": ["crossref_provisional_roster"]},
        }
        with self.assertRaisesRegex(ValueError, "DOI roster mismatch"):
            qualify_crossref_issue_with_econometric_society(candidate, roster)

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
