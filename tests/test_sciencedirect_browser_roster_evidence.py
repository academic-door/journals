from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import capture_sciencedirect_browser_roster_evidence as capture


class ScienceDirectBrowserRosterEvidenceTests(unittest.TestCase):
    def _staging_issue(self) -> dict:
        return {
            "journal_id": "wd",
            "issue_id": "wd-207-c",
            "volume": "207",
            "issue": "C",
            "publication_date": "November 2026",
            "articles": [
                {
                    "paper_id": "doi:10.1016/j.worlddev.2026.107507",
                    "doi": "10.1016/j.worlddev.2026.107507",
                    "title_en": "Weather-related shocks and economic growth in low-income countries: The role of aid",
                    "authors": ["Ada Lovelace"],
                    "source_url": "https://ideas.repec.org/a/eee/wdevel/example.html",
                    "article_type": "research-article",
                },
                {
                    "paper_id": "doi:10.1016/j.worlddev.2026.107513",
                    "doi": "10.1016/j.worlddev.2026.107513",
                    "title_en": "Piloting the local: How traditions and local power shape the evolution of community-driven development programs in Indonesia",
                    "authors": ["Grace Hopper"],
                    "source_url": "https://ideas.repec.org/a/eee/wdevel/editorial.html",
                    "article_type": "research-article",
                },
            ],
            "quality": {},
        }

    def _snapshot(self) -> dict:
        return {
            "journal_id": "wd",
            "issue_id": "wd-207-c",
            "official_url": "https://www.sciencedirect.com/journal/world-development/vol/207/suppl/C",
            "captured_at": "2026-09-17T00:00:00Z",
            "items": [
                {
                    "href": "https://www.sciencedirect.com/science/article/pii/S0305750X26002026",
                    "title": "Editorial Board",
                    "box_text": "Editorial",
                },
                {
                    "href": "https://www.sciencedirect.com/science/article/pii/S0305750X26002298",
                    "title": "Weather-related shocks and economic growth in low-income countries: The role of aid",
                    "box_text": "Research article",
                },
                {
                    "href": "https://www.sciencedirect.com/science/article/pii/S0305750X26002389",
                    "title": "Piloting the local: How traditions and local power shape the evolution of community-driven development programs in Indonesia",
                    "box_text": "Editorial",
                },
            ],
        }

    def test_build_evidence_uses_unique_exact_title_when_staging_has_no_pii(self) -> None:
        issue = self._staging_issue()
        evidence = capture.build_evidence(self._snapshot(), issue, excluded_dois={})

        self.assertEqual(
            ["10.1016/j.worlddev.2026.107507"],
            [item["doi"] for item in evidence["items"]],
        )
        self.assertEqual(2, evidence["excluded_item_count"])
        self.assertNotIn(
            "10.1016/j.worlddev.2026.107513",
            [item["doi"] for item in evidence["items"]],
        )

    def test_main_falls_back_to_staging_when_archive_is_missing(self) -> None:
        snapshot = self._snapshot()
        issue = self._staging_issue()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "wd-207-c.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            staging_path = root / "staging" / "wd" / "wd-207-c.json"
            staging_path.parent.mkdir(parents=True)
            staging_path.write_text(json.dumps(issue), encoding="utf-8")
            output_root = root / "evidence"

            argv = [
                "capture_sciencedirect_browser_roster_evidence.py",
                str(snapshot_path),
                "--api-root",
                str(root / "api"),
                "--staging-root",
                str(root / "staging"),
                "--output-root",
                str(output_root),
            ]
            with patch("sys.argv", argv):
                self.assertEqual(0, capture.main())

            self.assertTrue((output_root / "wd-207-c.json").exists())

    def test_title_fallback_fails_closed_when_candidate_titles_are_ambiguous(self) -> None:
        issue = self._staging_issue()
        issue["articles"].append(
            {
                **issue["articles"][0],
                "doi": "10.1016/j.worlddev.2026.999999",
                "paper_id": "doi:10.1016/j.worlddev.2026.999999",
            }
        )
        with self.assertRaisesRegex(ValueError, "ambiguous archive title"):
            capture.build_evidence(self._snapshot(), issue, excluded_dois={})


if __name__ == "__main__":
    unittest.main()
