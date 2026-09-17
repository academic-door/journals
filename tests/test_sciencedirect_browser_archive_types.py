from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from scripts.build_sciencedirect_browser_archives import build_rich_snapshot


class ScienceDirectBrowserArchiveTypeTests(unittest.TestCase):
    def test_structured_research_type_survives_section_heading_box_text(self) -> None:
        research_pii = "S0305750X26002858"
        roster = {
            "issue_id": "wd-207-c",
            "journal_id": "wd",
            "volume": "207",
            "issue": "c",
            "publication_date": "2026",
            "official_url": "https://www.sciencedirect.com/journal/world-development/vol/207/suppl/C",
            "captured_at": "2026-09-17T00:00:00+00:00",
            "items": [
                {
                    "href": f"/science/article/pii/{research_pii}",
                    "title": "A research paper",
                    "type": "research-article",
                    "box_text": "Regular Papers\nA research paper",
                },
                {
                    "href": "/science/article/pii/S0305750X26002999",
                    "title": "Editorial Board",
                    "type": "editorial",
                    "box_text": "Editorial Board",
                },
                {
                    "href": "/science/article/pii/S0305750X26003000",
                    "title": "Corrigendum",
                    "type": "erratum",
                    "box_text": "Corrigendum",
                },
            ],
        }
        metadata = {
            research_pii: {
                "doi": "10.1016/j.worlddev.2026.107500",
                "title_en": "A research paper",
                "authors": ["Author Example"],
                "abstract_en": "Example abstract.",
            }
        }

        with patch(
            "scripts.build_sciencedirect_browser_archives.fetch_issue_metadata",
            return_value=metadata,
        ) as fetch:
            snapshot = build_rich_snapshot(
                roster,
                session=requests.Session(),
                journal={"name": "World Development"},
            )

        self.assertEqual([research_pii], fetch.call_args.args[1])
        self.assertEqual(1, len(snapshot["items"]))
        self.assertEqual(research_pii, snapshot["items"][0]["pii"])


if __name__ == "__main__":
    unittest.main()
