from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from collectors.elsevier import (
    ElsevierCollectorError,
    _parse_repec_volume_sections,
    fetch_current_issue,
)
from scripts.update_journals import issue_source_status


class ElsevierRepecSourceAuthorityTests(unittest.TestCase):
    def test_publisher_supplied_repec_fallback_preserves_source_authority(self) -> None:
        inventory = {
            "volume": "207",
            "issue": "C",
            "year": "2026",
            "items": [
                {
                    "title_en": "Research paper",
                    "detail_url": "https://ideas.repec.org/a/eee/wdevel/example.html",
                    "pii": "S0305750X26000001",
                }
            ],
        }
        detail = {
            "pii": "S0305750X26000001",
            "title_en": "Research paper",
            "authors": ["Ada Lovelace"],
            "abstract_en": "A complete abstract.",
            "doi": "10.1016/j.worlddev.2026.100001",
            "source_url": "https://www.sciencedirect.com/science/article/pii/S0305750X26000001",
            "detail_url": "https://ideas.repec.org/a/eee/wdevel/example.html",
            "article_type": "research-article",
            "publication_date": "November 2026",
            "quality_flags": [],
        }

        with (
            patch("collectors.elsevier._session", return_value=SimpleNamespace()),
            patch(
                "collectors.elsevier._get",
                side_effect=[
                    SimpleNamespace(content=b"repec"),
                    ElsevierCollectorError("ScienceDirect blocked"),
                ],
            ),
            patch(
                "collectors.elsevier._parse_repec_inventory",
                return_value=inventory,
            ),
            patch(
                "collectors.elsevier._parse_repec_detail",
                return_value=detail,
            ),
            patch(
                "collectors.elsevier._crossref_issue_date",
                return_value="November 2026",
            ),
            patch(
                "collectors.elsevier._publication_date_within_horizon",
                return_value=True,
            ),
        ):
            issue = fetch_current_issue(
                journal_id="wd",
                journal_name="World Development",
                issn="0305-750X",
                repec_series_url="https://ideas.repec.org/s/eee/wdevel.html",
                issue_url_template=(
                    "https://www.sciencedirect.com/journal/world-development/"
                    "vol/{volume}/suppl/{issue}"
                ),
                rss_url="",
                expected_volume="207",
                expected_issue="C",
                max_workers=1,
            )

        self.assertEqual(
            "repec-publisher-supplied",
            issue["quality"]["roster_authority"],
        )
        self.assertEqual(
            "repec-serial-page",
            issue["quality"]["roster_transport"],
        )
        self.assertEqual("publisher_verified", issue_source_status(issue))


    def test_continuous_repec_volume_without_issue_token_is_issue_c(self) -> None:
        sections = _parse_repec_volume_sections(
            b"""<html><body>
            <h3>November 2026, Volume 62</h3>
            <div><a href="/a/red/issued/example.html">Research paper</a></div>
            </body></html>""",
            "https://ideas.repec.org/s/red/issued.html",
        )
        self.assertIn("62|c", sections)
        self.assertEqual("C", sections["62|c"]["issue"])
        self.assertEqual(1, len(sections["62|c"]["items"]))


if __name__ == "__main__":
    unittest.main()
