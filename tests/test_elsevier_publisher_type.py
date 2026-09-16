from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from collectors.elsevier import ElsevierCollectorError, fetch_current_issue
from collectors.metadata_fallback import _elsevier_lookup


class XmlResponse:
    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code
        self.headers: dict[str, str] = {}


class PublisherTypeSession:
    def get(self, url: str, **kwargs) -> XmlResponse:
        if "/content/metadata/article" in url:
            return XmlResponse(
                b"<response><entry><pubtype>edi</pubtype></entry></response>"
            )
        if "/content/search/sciencedirect" in url:
            return XmlResponse(b"<response />", status_code=400)
        if "/content/abstract/doi/" in url:
            return XmlResponse(
                b"""
                <abstracts-retrieval-response>
                  <coredata>
                    <subtype>ed</subtype>
                    <subtypeDescription>Editorial</subtypeDescription>
                  </coredata>
                </abstracts-retrieval-response>
                """
            )
        return XmlResponse(b"<response />")


class ElsevierPublisherTypeTests(unittest.TestCase):
    def test_elsevier_lookup_exposes_publisher_editorial_type(self) -> None:
        with patch.dict(
            os.environ,
            {"ELSEVIER_API_KEY": "test-key", "ELSEVIER_INST_TOKEN": "test-token"},
            clear=True,
        ):
            lookup = _elsevier_lookup(
                PublisherTypeSession(),
                "",
                doi="10.1016/j.landusepol.2026.108195",
                timeout=10,
            )

        self.assertEqual("editorial", lookup.get("article_type", ""))
        self.assertEqual("success_no_abstract", lookup["status"])
        self.assertEqual("", lookup["abstract"])

    def test_repec_fallback_excludes_publisher_classified_editorial(self) -> None:
        inventory = {
            "volume": "170",
            "issue": "C",
            "year": "2026",
            "items": [
                {"pii": "S0264837726000001", "title_en": "Research paper"},
                {
                    "pii": "S0264837726002796",
                    "title_en": "Securing rural land for whom, and for what? Measuring the impact of land interventions",
                },
            ],
        }
        details = [
            {
                "pii": "S0264837726000001",
                "title_en": "Research paper",
                "authors": ["Ada Lovelace"],
                "abstract_en": "A complete research abstract.",
                "doi": "10.1016/j.landusepol.2026.108194",
                "source_url": "https://www.sciencedirect.com/science/article/pii/S0264837726000001",
                "detail_url": "https://ideas.repec.org/a/eee/lauspo/example1.html",
            },
            {
                "pii": "S0264837726002796",
                "title_en": "Securing rural land for whom, and for what? Measuring the impact of land interventions",
                "authors": ["Thea Hilhorst", "Jaap Zevenbergen"],
                "abstract_en": "",
                "doi": "10.1016/j.landusepol.2026.108195",
                "source_url": "https://www.sciencedirect.com/science/article/pii/S0264837726002796",
                "detail_url": "https://ideas.repec.org/a/eee/lauspo/example2.html",
            },
        ]
        with (
            patch("collectors.elsevier._session", return_value=SimpleNamespace()),
            patch(
                "collectors.elsevier._get",
                side_effect=[
                    SimpleNamespace(content=b"serial"),
                    ElsevierCollectorError("publisher html blocked"),
                ],
            ),
            patch(
                "collectors.elsevier._parse_repec_inventory",
                return_value=inventory,
            ),
            patch(
                "collectors.elsevier._parse_repec_detail",
                side_effect=details,
            ),
            patch(
                "collectors.elsevier._crossref_issue_date",
                return_value="November 2026",
            ),
            patch(
                "collectors.elsevier._publication_date_within_horizon",
                return_value=True,
            ),
            patch(
                "collectors.metadata_fallback._elsevier_lookup",
                return_value={
                    "article_type": "editorial",
                    "abstract": "",
                    "source": "",
                    "status": "success_no_abstract",
                },
            ) as lookup,
        ):
            issue = fetch_current_issue(
                journal_id="lup",
                journal_name="Land Use Policy",
                issn="0264-8377",
                repec_series_url="https://ideas.repec.org/s/eee/lauspo.html",
                issue_url_template="https://www.sciencedirect.com/journal/land-use-policy/vol/{volume}/suppl/{issue}",
                publication_lead_months=2,
                expected_volume="170",
                max_workers=1,
            )

        self.assertEqual(["Research paper"], [a["title_en"] for a in issue["articles"]])
        self.assertEqual(1, issue["research_article_count"])
        self.assertEqual(1, issue["quality"]["excluded_item_count"])
        self.assertEqual(
            "10.1016/j.landusepol.2026.108195",
            issue["quality"]["excluded_items"][0]["doi"],
        )
        self.assertEqual("editorial_material", issue["quality"]["excluded_items"][0]["reason"])
        self.assertNotIn("abstract_en_incomplete", issue["quality"]["flags"])
        lookup.assert_called_once()

    def test_repec_fallback_uses_publisher_abstract_when_available(self) -> None:
        inventory = {
            "volume": "207",
            "issue": "C",
            "year": "2026",
            "items": [
                {"pii": "S0305750X26002031", "title_en": "Research paper"},
            ],
        }
        detail = {
            "pii": "S0305750X26002031",
            "title_en": "Research paper",
            "authors": ["Ada Lovelace"],
            "abstract_en": "",
            "doi": "10.1016/j.worlddev.2026.107512",
            "source_url": "https://www.sciencedirect.com/science/article/pii/S0305750X26002031",
            "detail_url": "https://ideas.repec.org/a/eee/wdevel/example.html",
        }
        with (
            patch("collectors.elsevier._session", return_value=SimpleNamespace()),
            patch(
                "collectors.elsevier._get",
                side_effect=[
                    SimpleNamespace(content=b"serial"),
                    ElsevierCollectorError("publisher html blocked"),
                ],
            ),
            patch("collectors.elsevier._parse_repec_inventory", return_value=inventory),
            patch("collectors.elsevier._parse_repec_detail", return_value=detail),
            patch("collectors.elsevier._crossref_issue_date", return_value="November 2026"),
            patch("collectors.elsevier._publication_date_within_horizon", return_value=True),
            patch(
                "collectors.metadata_fallback._elsevier_lookup",
                return_value={
                    "article_type": "research-article",
                    "abstract": "Recovered publisher abstract.",
                    "source": "elsevier-scopus-doi",
                    "status": "success_full_abstract",
                },
            ),
        ):
            issue = fetch_current_issue(
                journal_id="wd",
                journal_name="World Development",
                issn="0305-750X",
                repec_series_url="https://ideas.repec.org/s/eee/wdevel.html",
                issue_url_template="https://www.sciencedirect.com/journal/world-development/vol/{volume}/suppl/{issue}",
                publication_lead_months=2,
                expected_volume="207",
                max_workers=1,
            )

        self.assertEqual("Recovered publisher abstract.", issue["articles"][0]["abstract_en"])
        self.assertEqual(
            "elsevier-scopus-doi",
            issue["articles"][0]["sources"]["abstract_en"],
        )
        self.assertNotIn("abstract_en_incomplete", issue["quality"]["flags"])

    def test_research_report_title_is_not_reclassified_as_editorial(self) -> None:
        inventory = {
            "volume": "207",
            "issue": "C",
            "year": "2026",
            "items": [
                {
                    "pii": "S0305750X26002032",
                    "title_en": "A report on rural productivity and household welfare",
                },
            ],
        }
        detail = {
            "pii": "S0305750X26002032",
            "title_en": "A report on rural productivity and household welfare",
            "authors": ["Ada Lovelace"],
            "abstract_en": "This study reports evidence from a household panel.",
            "doi": "10.1016/j.worlddev.2026.107514",
            "source_url": "https://www.sciencedirect.com/science/article/pii/S0305750X26002032",
            "detail_url": "https://ideas.repec.org/a/eee/wdevel/example-report.html",
        }
        with (
            patch("collectors.elsevier._session", return_value=SimpleNamespace()),
            patch(
                "collectors.elsevier._get",
                side_effect=[
                    SimpleNamespace(content=b"serial"),
                    ElsevierCollectorError("publisher html blocked"),
                ],
            ),
            patch("collectors.elsevier._parse_repec_inventory", return_value=inventory),
            patch("collectors.elsevier._parse_repec_detail", return_value=detail),
            patch("collectors.elsevier._crossref_issue_date", return_value="November 2026"),
            patch("collectors.elsevier._publication_date_within_horizon", return_value=True),
            patch("collectors.metadata_fallback._elsevier_lookup") as lookup,
        ):
            issue = fetch_current_issue(
                journal_id="wd",
                journal_name="World Development",
                issn="0305-750X",
                repec_series_url="https://ideas.repec.org/s/eee/wdevel.html",
                issue_url_template="https://www.sciencedirect.com/journal/world-development/vol/{volume}/suppl/{issue}",
                publication_lead_months=2,
                expected_volume="207",
                max_workers=1,
            )

        self.assertEqual(1, issue["research_article_count"])
        self.assertEqual(
            "A report on rural productivity and household welfare",
            issue["articles"][0]["title_en"],
        )
        self.assertEqual(0, issue["quality"]["excluded_item_count"])
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
