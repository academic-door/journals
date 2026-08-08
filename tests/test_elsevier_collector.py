import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from collectors.elsevier import (
    ElsevierCollectorError,
    _crossref_issue_date,
    _normalize_authors,
    _parse_official_issue,
    _parse_repec_inventory,
    _publication_date_within_horizon,
    fetch_current_issue,
)


class ElsevierCollectorTests(unittest.TestCase):
    def test_ere_uses_official_issue_month_on_active_repec_path(self):
        self.assertEqual(
            "August 2026",
            _crossref_issue_date(
                SimpleNamespace(), "0924-6460", "94", "2026", "8"
            ),
        )

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

    def test_volume_only_repec_heading_falls_back_to_rss(self):
        """RED-style RePEc headings (August 2026, Volume 61) skip the
        inventory parser and use the publisher RSS feed instead."""

        rss_issue = {
            "issue_id": "red-61-c",
            "journal_id": "red",
            "journal_name": "Review of Economic Dynamics",
            "volume": "61",
            "issue": "C",
            "publication_date": "August 2026",
            "articles": [],
        }
        with (
            patch("collectors.elsevier._session"),
            patch(
                "collectors.elsevier._get",
                return_value=SimpleNamespace(content=b"red repec page"),
            ),
            patch(
                "collectors.elsevier._parse_repec_inventory",
                side_effect=ElsevierCollectorError(
                    "RePEc serial page has no usable volume heading"
                ),
            ),
            patch(
                "collectors.metadata_fallback.fetch_sciencedirect_rss_issue",
                return_value=rss_issue,
            ) as fetch_rss,
        ):
            result = fetch_current_issue(
                journal_id="red",
                journal_name="Review of Economic Dynamics",
                issn="1094-2025",
                repec_series_url="https://ideas.repec.org/s/red/issued.html",
                issue_url_template=(
                    "https://www.sciencedirect.com/journal/"
                    "review-of-economic-dynamics/vol/{volume}/suppl/{issue}"
                ),
                rss_url="https://rss.sciencedirect.com/publication/science/10942025",
            )

        self.assertEqual("red-61-c", result["issue_id"])
        fetch_rss.assert_called_once()

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



class RepecHistoryCollectorTests(unittest.TestCase):
    def test_repec_page_url_paginates(self) -> None:
        from collectors.elsevier import _repec_page_url

        self.assertEqual(
            "https://ideas.repec.org/s/eee/deveco.html",
            _repec_page_url("https://ideas.repec.org/s/eee/deveco.html", 1),
        )
        self.assertEqual(
            "https://ideas.repec.org/s/eee/deveco2.html",
            _repec_page_url("https://ideas.repec.org/s/eee/deveco.html", 2),
        )

    def test_fetch_elsevier_repec_history_issue_builds_complete_volume(self) -> None:
        from collectors.elsevier import fetch_elsevier_repec_history_issue

        page1 = b"""
        <html><body>
          <h3>2026, Volume 183, Issue C</h3>
          <div><ul>
            <li><a href="/a/eee/deveco/v183y2026ics0304387826000672.html">Current paper</a></li>
          </ul></div>
        </body></html>
        """
        page2 = b"""
        <html><body>
          <h3>2025, Volume 173, Issue C</h3>
          <div><ul>
            <li><a href="/a/eee/deveco/v173y2025ics0304387825000001.html">Paper one</a></li>
            <li><a href="/a/eee/deveco/v173y2025ics0304387825000002.html">Editorial Board</a></li>
            <li><a href="/a/eee/deveco/v173y2025ics0304387825000003.html">Paper two</a></li>
          </ul></div>
        </body></html>
        """
        pages = {1: page1, 2: page2}

        class Response:
            def __init__(self, content: bytes) -> None:
                self.content = content

            def raise_for_status(self) -> None:
                return None

        class Session:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def get(self, url: str, **kwargs) -> Response:
                self.calls.append(url)
                page = 2 if "deveco2" in url else 1
                return Response(pages[page])

        with (
            patch(
                "collectors.elsevier._parse_repec_detail",
                side_effect=[
                    {
                        "pii": "S0304387825000001",
                        "title_en": "Paper one",
                        "authors": ["Ada Lovelace"],
                        "abstract_en": "Abstract one.",
                        "doi": "10.1016/j.jdeveco.2024.103001",
                        "source_url": "https://www.sciencedirect.com/science/article/pii/S0304387825000001",
                        "publication_date": "May 2025",
                    },
                    {
                        "pii": "S0304387825000002",
                        "title_en": "Editorial Board",
                        "authors": [],
                        "abstract_en": "",
                        "doi": "",
                        "source_url": "https://www.sciencedirect.com/science/article/pii/S0304387825000002",
                        "publication_date": "May 2025",
                    },
                    {
                        "pii": "S0304387825000003",
                        "title_en": "Paper two",
                        "authors": ["Grace Hopper"],
                        "abstract_en": "Abstract two.",
                        "doi": "10.1016/j.jdeveco.2024.103002",
                        "source_url": "https://www.sciencedirect.com/science/article/pii/S0304387825000003",
                        "publication_date": "May 2025",
                    },
                ],
            ),
            patch("collectors.metadata_fallback._is_no_abstract_notice", return_value=False),
        ):
            issue = fetch_elsevier_repec_history_issue(
                journal_id="jde",
                journal_name="Journal of Development Economics",
                issn="0304-3878",
                volume="173",
                repec_series_url="https://ideas.repec.org/s/eee/deveco.html",
                session=Session(),
            )
        self.assertEqual("jde-173-c", issue["issue_id"])
        self.assertEqual(2, issue["research_article_count"])
        self.assertEqual(3, issue["quality"]["repec_item_count"])
        self.assertEqual(["Paper one", "Paper two"], [a["title_en"] for a in issue["articles"]])
        self.assertEqual([1, 2], [a["sequence"] for a in issue["articles"]])

    def test_repec_volume_sections_keyed_by_volume_and_issue(self) -> None:
        from collectors.elsevier import _parse_repec_volume_sections

        content = b"""
        <html><body>
          <h3>2025, Volume 53, Issue 2</h3>
          <div><ul>
            <li><a href="/a/eee/jcecon/v53y2025ics0147596725000081.html">Paper two-one</a></li>
          </ul></div>
          <h3>2025, Volume 53, Issue 1</h3>
          <div><ul>
            <li><a href="/a/eee/jcecon/v53y2025ics0147596725000011.html">Paper one-one</a></li>
          </ul></div>
          <h3>2026, Volume 173, Issue C</h3>
          <div><ul>
            <li><a href="/a/eee/deveco/v173y2026ics0304387826000001.html">Continuous paper</a></li>
          </ul></div>
        </body></html>
        """
        sections = _parse_repec_volume_sections(
            content,
            "https://ideas.repec.org/s/eee/jcecon.html",
        )
        self.assertEqual({"53|1", "53|2", "173|c"}, set(sections))
        self.assertEqual("1", sections["53|1"]["issue"])
        self.assertEqual("2", sections["53|2"]["issue"])

    def test_fetch_elsevier_repec_history_issue_matches_specific_issue(self) -> None:
        from collectors.elsevier import fetch_elsevier_repec_history_issue

        page1 = b"""
        <html><body>
          <h3>2026, Volume 54, Issue 2</h3>
          <div><ul>
            <li><a href="/a/eee/jcecon/v54y2026ics0147596726000101.html">Issue two paper</a></li>
          </ul></div>
          <h3>2026, Volume 54, Issue 1</h3>
          <div><ul>
            <li><a href="/a/eee/jcecon/v54y2026ics0147596726000011.html">Issue one paper</a></li>
          </ul></div>
        </body></html>
        """

        class Response:
            def __init__(self, content: bytes) -> None:
                self.content = content

            def raise_for_status(self) -> None:
                return None

        class Session:
            def get(self, url: str, **kwargs) -> Response:
                return Response(page1)

        with (
            patch(
                "collectors.elsevier._parse_repec_detail",
                return_value={
                    "pii": "S0147596726000101",
                    "title_en": "Issue two paper",
                    "authors": ["Jane Doe"],
                    "abstract_en": "Abstract.",
                    "doi": "10.1016/j.jce.2026.100001",
                    "source_url": "https://www.sciencedirect.com/science/article/pii/S0147596726000101",
                    "publication_date": "June 2026",
                },
            ),
            patch("collectors.metadata_fallback._is_no_abstract_notice", return_value=False),
        ):
            issue = fetch_elsevier_repec_history_issue(
                journal_id="jce",
                journal_name="Journal of Comparative Economics",
                issn="0147-5967",
                volume="54",
                issue="2",
                repec_series_url="https://ideas.repec.org/s/eee/jcecon.html",
                session=Session(),
            )
        self.assertEqual("jce-54-2", issue["issue_id"])
        self.assertEqual("2", issue["issue"])
        self.assertEqual(1, issue["research_article_count"])
        self.assertEqual(["Issue two paper"], [a["title_en"] for a in issue["articles"]])


if __name__ == "__main__":
    unittest.main()

