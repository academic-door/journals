import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from collectors.elsevier import fetch_current_issue
from scripts.update_journals import collector_for


class ElsevierExpectedTargetTests(unittest.TestCase):
    def test_fetch_current_issue_prefers_expected_volume_over_later_repec_preregistration(self) -> None:
        content = b"""
        <html><body>
          <h3>2026, Volume 171, Issue C</h3>
          <div><ul>
            <li><a href="/a/eee/lauspo/v171y2026ics0264837726002826.html">Future paper</a></li>
          </ul></div>
          <h3>2026, Volume 170, Issue C</h3>
          <div><ul>
            <li><a href="/a/eee/lauspo/v170y2026ics0264837726002231.html">Expected paper</a></li>
          </ul></div>
        </body></html>
        """
        detail = {
            "pii": "S0264837726002231",
            "title_en": "Expected paper",
            "authors": ["Ada Example"],
            "abstract_en": "Expected abstract.",
            "doi": "10.1016/j.landusepol.2026.100001",
            "source_url": "https://www.sciencedirect.com/science/article/pii/S0264837726002231",
            "detail_url": "https://ideas.repec.org/a/eee/lauspo/example.html",
            "article_type": "research-article",
            "publication_date": "November 2026",
        }
        kwargs = {
            "journal_id": "lup",
            "journal_name": "Land Use Policy",
            "issn": "0264-8377",
            "repec_series_url": "https://ideas.repec.org/s/eee/lauspo.html",
            "issue_url_template": "https://www.sciencedirect.com/journal/land-use-policy/vol/{volume}/suppl/{issue}",
            "rss_url": "https://rss.sciencedirect.com/publication/science/02648377",
            "publication_lead_months": 2,
        }
        if "expected_volume" in inspect.signature(fetch_current_issue).parameters:
            kwargs["expected_volume"] = "170"

        with (
            patch("collectors.elsevier._session"),
            patch(
                "collectors.elsevier._get",
                return_value=SimpleNamespace(content=content),
            ),
            patch(
                "collectors.metadata_fallback.fetch_sciencedirect_rss_issue",
                return_value=None,
            ),
            patch(
                "collectors.elsevier._crossref_issue_date",
                return_value="November 2026",
            ),
            patch(
                "collectors.elsevier._parse_repec_detail",
                return_value=detail,
            ),
        ):
            issue = fetch_current_issue(**kwargs)

        self.assertEqual("170", issue["volume"])
        self.assertEqual("lup-170-c", issue["issue_id"])

    def test_update_collector_forwards_expected_volume_to_elsevier(self) -> None:
        config = {
            "id": "lup",
            "name": "Land Use Policy",
            "collector": "elsevier",
            "issn": "0264-8377",
            "current_issue_url": "https://www.sciencedirect.com/journal/land-use-policy/issues",
            "repec_series_url": "https://ideas.repec.org/s/eee/lauspo.html",
            "issue_url_template": "https://www.sciencedirect.com/journal/land-use-policy/vol/{volume}/suppl/{issue}",
            "publication_lead_months": 2,
        }
        kwargs = {}
        if "expected_volume" in inspect.signature(collector_for).parameters:
            kwargs["expected_volume"] = "170"

        with patch(
            "collectors.elsevier.fetch_current_issue",
            return_value={"issue_id": "lup-170-c"},
        ) as fetch_issue:
            collector_for(config, **kwargs)()

        self.assertEqual("170", fetch_issue.call_args.kwargs.get("expected_volume"))


if __name__ == "__main__":
    unittest.main()
