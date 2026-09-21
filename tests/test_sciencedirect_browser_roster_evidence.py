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
        with patch.object(capture, "apply_evidence"):
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
            with patch("sys.argv", argv), patch.object(capture, "apply_evidence"):
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

    def test_doi_matched_publisher_subtitle_expansion_is_narrowly_accepted(self) -> None:
        issue = self._staging_issue()
        snapshot = self._snapshot()
        official = issue["articles"][0]["title_en"] + ": Publisher subtitle"
        snapshot["items"][1]["title"] = official
        snapshot["items"][1]["box_text"] = "Research article\\n" + official
        snapshot["items"][1]["doi"] = issue["articles"][0]["doi"]
        with patch.object(capture, "apply_evidence"):
            evidence = capture.build_evidence(snapshot, issue, excluded_dois={})

        self.assertEqual(issue["articles"][0]["title_en"], evidence["items"][0]["title_en"])
        self.assertEqual(official, evidence["items"][0]["official_display_title_en"])

    def test_explicit_nonresearch_types_are_excluded(self) -> None:
        issue = self._staging_issue()
        snapshot = self._snapshot()
        snapshot["items"].insert(
            1,
            {
                "href": "https://www.sciencedirect.com/science/article/pii/S0000000000000002",
                "title": "A Book Review",
                "box_text": "Book review\nA Book Review",
                "type": "book-review",
            },
        )
        snapshot["items"].insert(
            2,
            {
                "href": "https://www.sciencedirect.com/science/article/pii/S0000000000000003",
                "title": "Obituary: Example Scholar",
                "box_text": "Obituary\nObituary: Example Scholar",
                "type": "obituary",
            },
        )
        snapshot["items"].insert(
            3,
            {
                "href": "https://www.sciencedirect.com/science/article/pii/S0000000000000004",
                "title": "Publisher's Note",
                "box_text": "Publisher's Note",
                "type": "publisher-note",
            },
        )
        snapshot["items"].insert(
            4,
            {
                "href": "https://www.sciencedirect.com/science/article/pii/S0000000000000005",
                "title": "Publisher News",
                "box_text": "News\nPublisher News",
                "type": "news",
            },
        )
        snapshot["items"].insert(
            5,
            {
                "href": "https://www.sciencedirect.com/science/article/pii/S0000000000000006",
                "title": "Obituary: Another Scholar",
                "box_text": "Announcement\nObituary: Another Scholar",
                "type": "announcement",
            },
        )
        with patch.object(capture, "apply_evidence"):
            evidence = capture.build_evidence(snapshot, issue, excluded_dois={})

        reasons = {item["reason"] for item in evidence["excluded_items"]}
        self.assertIn("official-book-review", reasons)
        self.assertIn("official-obituary", reasons)
        self.assertIn("official-publisher-note", reasons)
        self.assertIn("official-news", reasons)
        self.assertIn("official-announcement", reasons)

    def test_mini_review_is_publishable(self) -> None:
        issue = self._staging_issue()
        snapshot = self._snapshot()
        snapshot["items"][1]["box_text"] = "Mini review\n" + snapshot["items"][1]["title"]
        snapshot["items"][1].pop("type", None)
        with patch.object(capture, "apply_evidence"):
            evidence = capture.build_evidence(snapshot, issue, excluded_dois={})
        self.assertEqual(issue["articles"][0]["doi"], evidence["items"][0]["doi"])

    def test_convert_batch_defers_one_issue_and_writes_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api_root = root / "api"
            staging_root = root / "staging"
            output_root = root / "evidence"
            snapshots = []
            for issue_id in ("wd-207-c", "wd-208-c"):
                snapshot_path = root / f"{issue_id}.json"
                payload = self._snapshot()
                payload["issue_id"] = issue_id
                snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
                snapshots.append(snapshot_path)
                issue_path = staging_root / "wd" / f"{issue_id}.json"
                issue_path.parent.mkdir(parents=True, exist_ok=True)
                issue_payload = self._staging_issue()
                issue_payload["issue_id"] = issue_id
                issue_path.write_text(json.dumps(issue_payload), encoding="utf-8")

            with patch.object(
                capture,
                "build_evidence",
                side_effect=[
                    ValueError("missing official research item lacks DOI/authors"),
                    {"issue_id": "wd-208-c", "items": []},
                ],
            ):
                written, deferred = capture.convert_batch(
                    snapshots,
                    api_root=api_root,
                    staging_root=staging_root,
                    output_root=output_root,
                    excluded_dois={},
                )

            self.assertEqual(["wd-208-c"], written)
            self.assertEqual("wd-207-c", deferred[0]["issue_id"])
            self.assertTrue((output_root / "wd-208-c.json").exists())
            self.assertFalse((output_root / "wd-207-c.json").exists())


if __name__ == "__main__":
    unittest.main()
