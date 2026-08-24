from __future__ import annotations

import unittest

from scripts.capture_sciencedirect_browser_roster_evidence import build_evidence


class ScienceDirectBrowserRosterCaptureTests(unittest.TestCase):
    def issue(self) -> dict:
        articles = []
        for sequence, pii in enumerate(("S0000000000000001", "S0000000000000002"), 1):
            articles.append(
                {
                    "paper_id": f"doi:10.1016/j.demo.2026.{sequence}",
                    "sequence": sequence,
                    "source_sequence": sequence,
                    "doi": f"10.1016/j.demo.2026.{sequence}",
                    "article_type": "research-article",
                    "title_en": f"Paper {sequence}",
                    "title_cn": f"论文{sequence}",
                    "authors": ["Author"],
                    "abstract_en": "Abstract 2026.",
                    "abstract_cn": "摘要 2026。",
                    "source_url": f"https://www.sciencedirect.com/science/article/pii/{pii}",
                    "sources": {"abstract_en": "official-sciencedirect-issue"},
                    "translation": {"status": "complete"},
                    "quality_flags": [],
                }
            )
        return {
            "schema_version": "1.0",
            "issue_id": "demo-1-c",
            "journal_id": "demo",
            "journal_name": "Demo",
            "volume": "1",
            "issue": "C",
            "publication_date": "2026",
            "source_url": "https://www.sciencedirect.com/journal/demo/vol/1/suppl/C",
            "retrieved_at": "2026-08-24T00:00:00+00:00",
            "expected_article_count": 2,
            "research_article_count": 2,
            "status": "incomplete",
            "articles": articles,
            "quality": {
                "roster_match": True,
                "order_preserved": True,
                "roster_authority": "crossref-provisional",
                "doi_complete": 2,
                "authors_complete": 2,
                "abstract_en_complete": 2,
                "translation_complete": 2,
                "duplicate_count": 0,
                "flags": ["crossref_provisional_roster"],
            },
        }

    def test_exact_pii_set_can_follow_official_browser_order(self) -> None:
        snapshot = {
            "issue_id": "demo-1-c",
            "journal_id": "demo",
            "official_url": "https://www.sciencedirect.com/journal/demo/vol/1/suppl/C",
            "captured_at": "2026-08-24T00:00:00+00:00",
            "items": [
                {
                    "href": "/science/article/pii/S0000000000000002",
                    "title": "Paper 2",
                    "type": "research-article",
                },
                {
                    "href": "/science/article/pii/S0000000000000001",
                    "title": "Paper 1",
                    "type": "research-article",
                },
                {
                    "href": "/science/article/pii/S0000000000000099",
                    "title": "Editorial Board",
                    "type": "editorial",
                },
            ],
        }
        evidence = build_evidence(
            snapshot,
            self.issue(),
            excluded_dois={"S0000000000000099": "10.1016/s0000-demo"},
        )
        self.assertTrue(evidence["allow_archive_reorder"])
        self.assertEqual(
            ["10.1016/j.demo.2026.2", "10.1016/j.demo.2026.1"],
            [item["doi"] for item in evidence["items"]],
        )

    def test_missing_or_unclassified_browser_item_fails_closed(self) -> None:
        snapshot = {
            "issue_id": "demo-1-c",
            "journal_id": "demo",
            "official_url": "https://www.sciencedirect.com/journal/demo/vol/1/suppl/C",
            "captured_at": "2026-08-24T00:00:00+00:00",
            "items": [
                {
                    "href": "/science/article/pii/S0000000000000001",
                    "title": "Paper 1",
                    "type": "research-article",
                },
                {
                    "href": "/science/article/pii/S0000000000000002",
                    "title": "Paper 2",
                    "type": "unknown",
                },
            ],
        }
        with self.assertRaisesRegex(ValueError, "unclassified"):
            build_evidence(snapshot, self.issue(), excluded_dois={})

    def test_structured_browser_snapshot_keeps_missing_article_for_api_enrichment(
        self,
    ) -> None:
        snapshot = {
            "issue_id": "demo-1-c",
            "journal_id": "demo",
            "official_url": "https://www.sciencedirect.com/journal/demo/vol/1/suppl/C",
            "captured_at": "2026-08-24T00:00:00+00:00",
            "items": [
                {
                    "href": "/science/article/pii/S0000000000000002",
                    "doi": "10.1016/j.demo.2026.2",
                    "title": "Paper 2",
                    "authors": ["Author"],
                    "box_text": "Research articleOpen access Paper 2",
                },
                {
                    "href": "/science/article/pii/S0000000000000003",
                    "doi": "10.1016/j.demo.2026.3",
                    "title": "New Official Paper",
                    "authors": ["New Author"],
                    "box_text": "Research articleAbstract only New Official Paper",
                },
                {
                    "href": "/science/article/pii/S0000000000000001",
                    "doi": "10.1016/j.demo.2026.1",
                    "title": "Paper 1",
                    "authors": ["Author"],
                    "box_text": "Research articleOpen access Paper 1",
                },
                {
                    "href": "/science/article/pii/S0000000000000099",
                    "doi": "10.1016/s0000-demo",
                    "title": "Editorial Board",
                    "authors": [],
                    "box_text": "Editorial boardFree access Editorial Board",
                },
            ],
        }
        evidence = build_evidence(snapshot, self.issue(), excluded_dois={})
        missing = evidence["items"][1]
        self.assertEqual("pii:S0000000000000003", missing["source_id"])
        self.assertEqual(["New Author"], missing["official_authors"])
        self.assertEqual(1, evidence["excluded_item_count"])
        self.assertEqual(
            "pii:S0000000000000099",
            evidence["excluded_items"][0]["source_id"],
        )


if __name__ == "__main__":
    unittest.main()
