from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from collectors.elsevier import fetch_elsevier_repec_history_issue


class ElsevierHistoryPublisherTypeTests(unittest.TestCase):
    def _fetch(self, lookup: dict[str, str]) -> dict:
        entry = {
            "pii": "S0305750X26002042",
            "title_en": "Identifying the types of government interventions that work in the sanitation sector",
            "detail_url": "https://ideas.repec.org/a/eee/wdevel/example-editorial.html",
        }
        detail = {
            "pii": entry["pii"],
            "title_en": entry["title_en"],
            "authors": ["Britta Augsburg", "Habiba Djebbari"],
            "abstract_en": "",
            "doi": "10.1016/j.worlddev.2026.107513",
            "source_url": "https://www.sciencedirect.com/science/article/pii/S0305750X26002042",
            "detail_url": entry["detail_url"],
            "publication_date": "2026-11-01",
        }
        with (
            patch(
                "collectors.elsevier._get",
                return_value=SimpleNamespace(content=b"serial"),
            ),
            patch(
                "collectors.elsevier._parse_repec_volume_sections",
                return_value={
                    "207|c": {
                        "year": "2026",
                        "issue": "C",
                        "items": [entry],
                    }
                },
            ),
            patch("collectors.elsevier._parse_repec_detail", return_value=detail),
            patch(
                "collectors.metadata_fallback._is_elsevier_identifier",
                return_value=True,
            ),
            patch(
                "collectors.metadata_fallback._elsevier_lookup",
                return_value=lookup,
            ) as publisher_lookup,
        ):
            issue = fetch_elsevier_repec_history_issue(
                journal_id="wd",
                journal_name="World Development",
                issn="0305-750X",
                volume="207",
                issue="C",
                repec_series_url="https://ideas.repec.org/s/eee/wdevel.html",
                max_pages=1,
                session=SimpleNamespace(),
            )

        publisher_lookup.assert_called_once()
        return issue

    def test_history_fallback_propagates_explicit_publisher_editorial_type(self) -> None:
        issue = self._fetch(
            {
                "article_type": "editorial",
                "abstract": "",
                "source": "elsevier-scopus-doi",
                "status": "success_no_abstract",
            }
        )

        self.assertEqual(1, len(issue["articles"]))
        self.assertEqual("editorial", issue["articles"][0]["article_type"])

    def test_history_fallback_keeps_unknown_missing_abstract_fail_closed(self) -> None:
        issue = self._fetch(
            {
                "article_type": "",
                "abstract": "",
                "source": "",
                "status": "not_found",
            }
        )

        self.assertEqual(1, len(issue["articles"]))
        self.assertEqual("research-article", issue["articles"][0]["article_type"])
        self.assertEqual("", issue["articles"][0]["abstract_en"])
        self.assertEqual("blocked", issue["articles"][0]["translation"]["status"])


if __name__ == "__main__":
    unittest.main()
