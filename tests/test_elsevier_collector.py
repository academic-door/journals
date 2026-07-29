import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from collectors.elsevier import (
    ElsevierCollectorError,
    _normalize_authors,
    _parse_official_issue,
    _parse_repec_inventory,
    _publication_date_within_horizon,
    fetch_current_issue,
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

    def test_publisher_rss_wins_over_later_repec_preregistration(self):
        repec_inventory = {
            "volume": "163",
            "issue": "C",
            "year": "2026",
            "items": [],
        }
        rss_issue = {
            "issue_id": "jie-162-c",
            "volume": "162",
            "publication_date": "August 2026",
        }
        with (
            patch("collectors.elsevier._session"),
            patch(
                "collectors.elsevier._get",
                return_value=SimpleNamespace(content=b"repec"),
            ),
            patch(
                "collectors.elsevier._parse_repec_inventory",
                return_value=repec_inventory,
            ),
            patch(
                "collectors.metadata_fallback.fetch_sciencedirect_rss_issue",
                return_value=rss_issue,
            ) as fetch_rss,
            patch("collectors.elsevier._parse_repec_detail") as parse_detail,
        ):
            result = fetch_current_issue(
                journal_id="jie",
                journal_name="Journal of International Economics",
                issn="0022-1996",
                repec_series_url="https://ideas.repec.org/s/eee/inecon.html",
                issue_url_template=(
                    "https://www.sciencedirect.com/journal/"
                    "journal-of-international-economics/vol/{volume}/suppl/{issue}"
                ),
                rss_url="https://rss.sciencedirect.com/publication/science/00221996",
            )

        self.assertEqual("jie-162-c", result["issue_id"])
        self.assertNotIn("newer_than_volume", fetch_rss.call_args.kwargs)
        parse_detail.assert_not_called()

    def test_repec_fallback_rejects_known_future_issue(self):
        repec_inventory = {
            "volume": "163",
            "issue": "C",
            "year": "2026",
            "items": [],
        }
        with (
            patch("collectors.elsevier._session"),
            patch(
                "collectors.elsevier._get",
                return_value=SimpleNamespace(content=b"repec"),
            ),
            patch(
                "collectors.elsevier._parse_repec_inventory",
                return_value=repec_inventory,
            ),
            patch(
                "collectors.metadata_fallback.fetch_sciencedirect_rss_issue",
                return_value=None,
            ),
            patch(
                "collectors.elsevier._crossref_issue_date",
                return_value="October 2026",
            ),
            patch(
                "collectors.elsevier._publication_date_within_horizon",
                return_value=False,
            ),
        ):
            with self.assertRaisesRegex(
                ElsevierCollectorError,
                "outside the configured publication horizon",
            ):
                fetch_current_issue(
                    journal_id="jie",
                    journal_name="Journal of International Economics",
                    issn="0022-1996",
                    repec_series_url="https://ideas.repec.org/s/eee/inecon.html",
                    issue_url_template=(
                        "https://www.sciencedirect.com/journal/"
                        "journal-of-international-economics/vol/{volume}/suppl/{issue}"
                    ),
                    rss_url="https://rss.sciencedirect.com/publication/science/00221996",
                )

    def test_publication_horizon_allows_only_next_month(self):
        today = date(2026, 7, 29)
        self.assertTrue(
            _publication_date_within_horizon(
                "August 2026",
                lead_months=1,
                today=today,
            )
        )
        self.assertFalse(
            _publication_date_within_horizon(
                "October 2026",
                lead_months=1,
                today=today,
            )
        )
        self.assertTrue(
            _publication_date_within_horizon(
                "2026",
                lead_months=1,
                today=today,
            )
        )


if __name__ == "__main__":
    unittest.main()
