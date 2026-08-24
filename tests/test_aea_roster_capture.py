from __future__ import annotations

import unittest

from scripts.capture_aea_roster_evidence import (
    build_evidence,
    parse_archive_links,
    parse_issue_roster,
)


class AEARosterCaptureTests(unittest.TestCase):
    def test_archive_maps_current_issue_ids_by_volume_and_number(self) -> None:
        html = """
        <a href="/issues/851">July 2026 (Vol. 18, No. 3)</a>
        <a href="/issues/840">April 2026 (Vol. 18, No. 2)</a>
        """
        self.assertEqual(
            {
                ("18", "3"): "https://www.aeaweb.org/issues/851",
                ("18", "2"): "https://www.aeaweb.org/issues/840",
            },
            parse_archive_links(html),
        )

    def test_issue_roster_keeps_research_order_and_excludes_front_matter(self) -> None:
        html = """
        <article class="journal-article" id="10.1257/app.1.1.i">
          <h3 class="title"><a>Front Matter</a></h3>
        </article>
        <article class="journal-article"><h3>Articles</h3></article>
        <article class="journal-article" id="10.1257/app.123">
          <h3 class="title"><a>First Paper</a></h3>
        </article>
        <article class="journal-article" id="10.1257/app.456">
          <h3 class="title"><a>Second Paper</a></h3>
        </article>
        """
        items, excluded = parse_issue_roster(html)
        self.assertEqual(1, len(excluded))
        self.assertEqual(
            ["10.1257/app.123", "10.1257/app.456"],
            [item["doi"] for item in items],
        )

    def test_correctional_sentencing_is_not_misclassified_as_correction(self) -> None:
        html = """
        <article class="journal-article" id="10.1257/pol.20220227">
          <h3 class="title"><a>Mental Health Consequences of Correctional Sentencing</a></h3>
        </article>
        """
        items, excluded = parse_issue_roster(html)
        self.assertEqual(["10.1257/pol.20220227"], [item["doi"] for item in items])
        self.assertEqual([], excluded)

    def test_evidence_uses_canonical_official_issue_url(self) -> None:
        record = {"journal": "AEJAPP", "issue_id": "aejapp-18-3"}
        html = """
        <article class="journal-article" id="10.1257/app.123">
          <h3 class="title"><a>Paper</a></h3>
        </article>
        """
        evidence = build_evidence(
            record,
            official_url="https://www.aeaweb.org/issues/851",
            html=html,
            captured_at="2026-08-24T00:00:00+00:00",
        )
        self.assertEqual("aejapp", evidence["journal_id"])
        self.assertEqual("https://www.aeaweb.org/issues/851", evidence["official_url"])


if __name__ == "__main__":
    unittest.main()
