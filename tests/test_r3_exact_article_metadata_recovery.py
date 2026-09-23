from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

import requests

from scripts.recover_trusted_elsevier_staging import (
    browser_snapshot_pii_by_title,
    enrich_missing_elsevier_abstracts,
)


class BrowserSnapshotPIIRecoveryTests(unittest.TestCase):
    def test_exact_browser_title_supplies_pii_only_for_metadata_lookup(self) -> None:
        issue_url = "https://www.sciencedirect.com/journal/demo/vol/1/suppl/C"
        candidate = {
            "issue_id": "demo-1-c",
            "journal_id": "demo",
            "source_url": issue_url,
            "quality": {
                "roster_match": True,
                "order_preserved": True,
                "roster_authority": "official-issue-page",
                "roster_transport": "browser-authorized",
                "flags": ["abstract_en_incomplete"],
            },
            "articles": [
                {
                    "article_type": "research-article",
                    "doi": "10.1016/j.demo.2026.000001",
                    "title_en": "A demo article",
                    "authors": ["Author One"],
                    "abstract_en": "",
                    "source_url": issue_url,
                    "sources": {
                        "issue": issue_url,
                        "roster": issue_url,
                        "metadata": "https://doi.org/10.1016/j.demo.2026.000001",
                        "abstract_en": "",
                    },
                }
            ],
        }
        journal = {
            "id": "demo",
            "publisher": "Elsevier",
            "repec_series_url": "https://ideas.repec.org/s/eee/demo.html",
        }

        with tempfile.TemporaryDirectory() as temporary:
            snapshot_root = Path(temporary)
            snapshot = snapshot_root / "sciencedirect" / "demo-1-c.json"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(
                json.dumps(
                    {
                        "journal_id": "demo",
                        "issue_id": "demo-1-c",
                        "official_url": issue_url,
                        "capture_mode": "browser-authorized",
                        "items": [
                            {
                                "title": "A demo article",
                                "href": "https://www.sciencedirect.com/science/article/pii/S0000000000000001",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                {"a demo article": "S0000000000000001"},
                browser_snapshot_pii_by_title(
                    candidate,
                    snapshot_root=snapshot_root,
                ),
            )
            with patch(
                "scripts.recover_trusted_elsevier_staging._elsevier_lookup",
                return_value={
                    "abstract": "Publisher abstract text.",
                    "source_url": "https://api.elsevier.com/content/article/pii/S0000000000000001",
                },
            ) as lookup:
                filled = enrich_missing_elsevier_abstracts(
                    candidate,
                    journal,
                    session=requests.Session(),
                    timeout=10,
                    snapshot_root=snapshot_root,
                )

        self.assertEqual(1, filled)
        self.assertEqual(
            "official-issue-page",
            candidate["quality"]["roster_authority"],
        )
        lookup.assert_called_once_with(
            ANY,
            "S0000000000000001",
            doi="10.1016/j.demo.2026.000001",
            timeout=10,
        )


if __name__ == "__main__":
    unittest.main()
