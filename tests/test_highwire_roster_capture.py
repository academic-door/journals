from __future__ import annotations

import unittest

from scripts.capture_highwire_roster_evidence import (
    build_evidence,
    parse_highwire_detail,
    parse_highwire_detail_urls,
    parse_highwire_roster,
)


class HighWireRosterCaptureTests(unittest.TestCase):
    HTML = """
    <div class="highwire-cite-highwire-article">
      <a class="highwire-cite-linked-title" href="/content/99/2/161">
        <span class="highwire-cite-title">First Research Paper</span>
        <span class="highwire-cite-subtitle">A subtitle</span>
      </a>
      <span class="highwire-cite-metadata-doi">DOI: https://doi.org/10.3368/le.demo1</span>
    </div>
    <div class="highwire-cite-highwire-article">
      <span class="highwire-cite-title">Front Matter</span>
      <span class="highwire-cite-metadata-doi">DOI: 10.3368/le.front</span>
    </div>
    <div class="highwire-cite-highwire-article">
      <span class="highwire-cite-title">Second Research Paper</span>
      <span class="highwire-cite-metadata-doi">DOI: 10.3368/le.demo2</span>
    </div>
    """

    def test_parser_keeps_official_main_title_order_and_exclusions(self) -> None:
        items, excluded = parse_highwire_roster(self.HTML)
        self.assertEqual(
            ["10.3368/le.demo1", "10.3368/le.demo2"],
            [item["doi"] for item in items],
        )
        self.assertEqual("First Research Paper", items[0]["title_en"])
        self.assertEqual(["10.3368/le.front"], [item["doi"] for item in excluded])

    def test_evidence_uses_concrete_official_issue_url(self) -> None:
        evidence = build_evidence(
            {"journal": "LANDECON", "issue_id": "landecon-99-2"},
            official_url="https://le.uwpress.org/content/99/2",
            html=self.HTML,
            captured_at="2026-08-24T00:00:00+00:00",
        )
        self.assertEqual("landecon", evidence["journal_id"])
        self.assertEqual("https://le.uwpress.org/content/99/2", evidence["official_url"])

    def test_parser_excludes_editor_notes_introductions_and_erratum_section(self) -> None:
        html = """
        <h2 class="toc-heading editorial">Editorial</h2>
        <div class="highwire-cite-highwire-article">
          <span class="highwire-cite-title">Editor's Note</span>
          <span class="highwire-cite-metadata-doi">DOI: 10.3368/le.note</span>
        </div>
        <div class="highwire-cite-highwire-article">
          <span class="highwire-cite-title">Introduction to the Special Issue</span>
          <span class="highwire-cite-metadata-doi">DOI: 10.3368/le.intro</span>
        </div>
        <h2 class="toc-heading research-article">Research Articles</h2>
        <div class="highwire-cite-highwire-article">
          <span class="highwire-cite-title">A Research Result</span>
          <span class="highwire-cite-metadata-doi">DOI: 10.3368/le.research</span>
        </div>
        <h2 class="toc-heading erratum">Erratum</h2>
        <div class="highwire-cite-highwire-article">
          <span class="highwire-cite-title">Estimating Consumer Willingness</span>
          <span class="highwire-cite-metadata-doi">DOI: 10.3368/le.99.4.iii</span>
        </div>
        """
        items, excluded = parse_highwire_roster(html)
        self.assertEqual(["10.3368/le.research"], [item["doi"] for item in items])
        self.assertEqual(
            ["10.3368/le.note", "10.3368/le.intro", "10.3368/le.99.4.iii"],
            [item["doi"] for item in excluded],
        )
        self.assertEqual(
            "non-research-section:Erratum",
            excluded[-1]["reason"],
        )

    def test_detail_url_and_metadata_are_read_from_official_pages(self) -> None:
        urls = parse_highwire_detail_urls(self.HTML)
        self.assertEqual(
            "https://le.uwpress.org/content/99/2/161",
            urls["10.3368/le.demo1"],
        )
        detail = parse_highwire_detail(
            """
            <meta name="citation_author" content="Author One">
            <meta name="citation_author" content="Author Two">
            <section class="abstract">Abstract Official abstract text.</section>
            """,
            source_url="https://le.uwpress.org/content/99/2/161",
        )
        self.assertEqual(["Author One", "Author Two"], detail["authors"])
        self.assertEqual("Official abstract text.", detail["abstract_en"])


if __name__ == "__main__":
    unittest.main()
