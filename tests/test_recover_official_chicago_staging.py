from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from scripts.recover_official_chicago_staging import (
    enrich_missing_repec_abstracts,
    trusted_chicago_staging,
)


class RecoverOfficialChicagoStagingTests(unittest.TestCase):
    def candidate(self) -> dict:
        return {
            "issue_id": "jaere-11-1",
            "journal_id": "jaere",
            "source_url": "https://journals.uchicago.edu/toc/jaere/2024/11/1",
            "source_status": "official_verified",
            "quality": {
                "roster_match": True,
                "order_preserved": True,
                "roster_authority": "official-issue-page",
                "roster_transport": "browser-authorized",
            },
            "articles": [
                {
                    "article_type": "research-article",
                    "doi": "10.1086/725699",
                    "title_en": "California’s GHG Cap-and-Trade Program and the Equity of Air Toxic Releases",
                    "authors": ["Glenn Sheriff"],
                    "abstract_en": "",
                    "sources": {
                        "issue": "https://journals.uchicago.edu/toc/jaere/2024/11/1",
                        "roster": "https://journals.uchicago.edu/toc/jaere/2024/11/1",
                        "metadata": "https://doi.org/10.1086/725699",
                    },
                }
            ],
        }

    def journal(self) -> dict:
        return {
            "id": "jaere",
            "publisher": "University of Chicago Press",
            "repec_series_code": "ucp/jaerec",
        }

    def test_repec_only_enriches_already_authoritative_browser_staging(self) -> None:
        candidate = self.candidate()
        repec_url = "https://ideas.repec.org/a/ucp/jaerec/doi_10.1086_725699.html"
        with patch(
            "scripts.recover_official_chicago_staging._repec_abstract",
            return_value=("Publisher-supplied abstract.", repec_url),
        ) as repec:
            filled = enrich_missing_repec_abstracts(
                candidate,
                self.journal(),
                session=requests.Session(),
                timeout=10,
            )

        self.assertEqual(1, filled)
        self.assertEqual(
            "official-issue-page", candidate["quality"]["roster_authority"]
        )
        self.assertEqual(
            "browser-authorized", candidate["quality"]["roster_transport"]
        )
        article = candidate["articles"][0]
        self.assertEqual("Publisher-supplied abstract.", article["abstract_en"])
        self.assertEqual(repec_url, article["sources"]["abstract_en"])
        self.assertEqual(repec_url, article["sources"]["repec"])
        repec.assert_called_once()

    def test_non_authoritative_staging_cannot_use_repec_to_gain_authority(self) -> None:
        candidate = self.candidate()
        candidate["source_status"] = "source_pending"
        candidate["quality"]["roster_authority"] = "crossref-provisional"
        candidate["quality"]["roster_transport"] = "crossref"

        self.assertFalse(trusted_chicago_staging(candidate, self.journal()))
        with patch(
            "scripts.recover_official_chicago_staging._repec_abstract"
        ) as repec:
            filled = enrich_missing_repec_abstracts(
                candidate,
                self.journal(),
                session=requests.Session(),
                timeout=10,
            )

        self.assertEqual(0, filled)
        repec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
