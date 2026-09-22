from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from scripts.recover_trusted_elsevier_staging import (
    enrich_missing_elsevier_abstracts,
    trusted_elsevier_staging,
)


class RecoverTrustedElsevierStagingTests(unittest.TestCase):
    def journal(self) -> dict:
        return {
            "id": "foodpolicy",
            "publisher": "Elsevier",
        }

    def candidate(self) -> dict:
        return {
            "issue_id": "foodpolicy-134-c",
            "journal_id": "foodpolicy",
            "source_url": "https://ideas.repec.org/s/eee/jfpoli.html",
            "quality": {
                "roster_match": True,
                "order_preserved": True,
                "roster_authority": "repec-publisher-supplied",
                "roster_transport": "repec-serial-page",
                "flags": ["abstract_en_incomplete", "translation_incomplete"],
            },
            "articles": [
                {
                    "article_type": "research-article",
                    "doi": "10.1016/j.foodpol.2025.102890",
                    "title_en": "The policy relevance of maximum residue limits analyses",
                    "authors": ["Author One"],
                    "abstract_en": "",
                    "source_url": "https://www.sciencedirect.com/science/article/pii/S0306919225000958",
                    "sources": {
                        "issue": "https://ideas.repec.org/s/eee/jfpoli.html",
                        "roster": "repec-serial-page",
                        "metadata": "repec-publisher-supplied",
                        "abstract_en": "",
                    },
                }
            ],
        }

    def test_accepts_existing_publisher_verified_repec_roster(self) -> None:
        candidate = self.candidate()
        self.assertTrue(trusted_elsevier_staging(candidate, self.journal()))

    def test_accepts_existing_official_browser_roster(self) -> None:
        candidate = self.candidate()
        candidate["source_url"] = (
            "https://www.sciencedirect.com/journal/food-policy/vol/134/suppl/C"
        )
        candidate["quality"]["roster_authority"] = "official-issue-page"
        candidate["quality"]["roster_transport"] = "browser-authorized"
        self.assertTrue(trusted_elsevier_staging(candidate, self.journal()))

    def test_rejects_crossref_provisional_staging(self) -> None:
        candidate = self.candidate()
        candidate["quality"]["roster_authority"] = "crossref-provisional"
        candidate["quality"]["roster_transport"] = "crossref"
        candidate["quality"]["flags"].append("crossref_provisional_roster")
        self.assertFalse(trusted_elsevier_staging(candidate, self.journal()))

    def test_elsevier_lookup_only_fills_missing_abstract_and_preserves_roster(self) -> None:
        candidate = self.candidate()
        source_url = (
            "https://api.elsevier.com/content/article/doi/"
            "10.1016/j.foodpol.2025.102890"
        )
        with patch(
            "scripts.recover_trusted_elsevier_staging._elsevier_lookup",
            return_value={
                "abstract": "Publisher abstract text.",
                "source_url": source_url,
                "source": "elsevier-article-doi",
                "status": "success_full_abstract",
            },
        ) as lookup:
            filled = enrich_missing_elsevier_abstracts(
                candidate,
                self.journal(),
                session=requests.Session(),
                timeout=10,
            )

        self.assertEqual(1, filled)
        article = candidate["articles"][0]
        self.assertEqual("Publisher abstract text.", article["abstract_en"])
        self.assertEqual(
            "official-elsevier-metadata",
            article["sources"]["abstract_en"],
        )
        self.assertEqual(source_url, article["sources"]["abstract_en_url"])
        self.assertEqual(
            "repec-publisher-supplied",
            candidate["quality"]["roster_authority"],
        )
        self.assertEqual(
            "repec-serial-page",
            candidate["quality"]["roster_transport"],
        )
        lookup.assert_called_once_with(
            unittest.mock.ANY,
            "S0306919225000958",
            doi="10.1016/j.foodpol.2025.102890",
            timeout=10,
        )


if __name__ == "__main__":
    unittest.main()
