import unittest

from collectors.elsevier import (
    _normalize_authors,
    _parse_official_issue,
    _parse_repec_inventory,
)


class ElsevierCollectorTests(unittest.TestCase):
    def test_repec_inventory_uses_latest_volume_and_preserves_order(self):
        content = b"""
        <html><body>
          <h3>2026, Volume 182, Issue C</h3>
          <div class="panel-body"><ul class="paperlist">
            <li><a href="/a/eee/deveco/v182y2026ics0304387826000362.html">First paper</a></li>
            <li><a href="/a/eee/deveco/v182y2026ics0304387826000465.html">Second paper</a></li>
          </ul></div>
          <h3>2026, Volume 181, Issue C</h3>
        </body></html>
        """
        result = _parse_repec_inventory(
            content,
            "https://ideas.repec.org/s/eee/deveco.html",
        )
        self.assertEqual(("182", "C"), (result["volume"], result["issue"]))
        self.assertEqual(
            ["First paper", "Second paper"],
            [item["title_en"] for item in result["items"]],
        )
        self.assertEqual("S0304387826000362", result["items"][0]["pii"])

    def test_official_sciencedirect_page_preserves_card_order(self):
        content = b"""
        <ol>
          <li class="js-article-list-item">
            <div hidden>https://doi.org/10.1016/j.test.2026.1</div>
            <a href="https://www.sciencedirect.com/science/article/pii/S0304387826000362">
              <span class="js-article-title">First paper</span>
            </a>
            <div class="js-article__item__authors">Alice Alpha, Bob Beta</div>
            <div class="js-article-subtype">Research article</div>
            <div class="js-abstract-body-text"><h5>Abstract</h5><p>First abstract.</p></div>
          </li>
          <li class="js-article-list-item">
            <div hidden>https://doi.org/10.1016/j.test.2026.2</div>
            <a href="https://www.sciencedirect.com/science/article/pii/S0304387826000465">
              <span class="js-article-title">Second paper</span>
            </a>
            <div class="js-article__item__authors">Cara Gamma</div>
            <div class="js-abstract-body-text"><p>Second abstract.</p></div>
          </li>
        </ol>
        """
        rows = _parse_official_issue(content)
        self.assertEqual(["First paper", "Second paper"], [row["title_en"] for row in rows])
        self.assertEqual("S0304387826000362", rows[0]["pii"])
        self.assertEqual("10.1016/j.test.2026.1", rows[0]["doi"])
        self.assertEqual(["Alice Alpha", "Bob Beta"], rows[0]["authors"])
        self.assertEqual("First abstract.", rows[0]["abstract_en"])

    def test_repec_author_names_are_normalized(self):
        self.assertEqual(
            ["Esther Duflo", "Daniel Keniston"],
            _normalize_authors("Duflo, Esther; Keniston, Daniel"),
        )


if __name__ == "__main__":
    unittest.main()
