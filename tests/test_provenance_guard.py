from __future__ import annotations

import unittest

from scripts.update_journals import preserve_existing_content


def make_issue(issue_id="demo-1-c", dois=("10.1/a", "10.1/b"), flags=()):
    return {
        "issue_id": issue_id,
        "research_article_count": len(dois),
        "articles": [
            {
                "doi": doi,
                "title_en": f"Title {doi}",
                "abstract_en": f"Abstract {doi}.",
                "abstract_cn": f"摘要 {doi}。",
                "authors": ["Author"],
                "sources": {},
                "quality_flags": [],
                "translation": {"status": "complete"},
            }
            for doi in dois
        ],
        "quality": {"flags": list(flags)},
    }


class ProvenanceGuardTests(unittest.TestCase):
    def test_verified_provenance_survives_same_roster_recollection(self):
        existing = make_issue(flags=())
        existing["quality"].update(
            {
                "roster_authority": "official-issue-page",
                "roster_transport": "official-issue-page+browser-authorized",
                "order_verification": "official_verified",
                "browser_order_verification": {
                    "pii_sequence_matched": True,
                    "verified_at": "2026-08-03T00:00:00+00:00",
                },
                "excluded_items": [
                    {
                        "title_en": "Editorial Board",
                        "doi": "10.1/x",
                        "reason": "official_abstract_not_provided",
                    }
                ],
            }
        )
        refreshed = make_issue(
            flags=(
                "publisher_html_blocked_sciencedirect_rss_fallback",
                "publisher_rss_reverse_order_normalized",
                "official_order_unverified",
                "elsevier_insttoken_required",
            )
        )
        result = preserve_existing_content(refreshed, existing)
        flags = result["quality"]["flags"]
        self.assertNotIn("official_order_unverified", flags)
        self.assertNotIn("publisher_html_blocked_sciencedirect_rss_fallback", flags)
        self.assertEqual(
            "official-issue-page", result["quality"]["roster_authority"]
        )
        self.assertEqual(
            "official_verified", result["quality"]["order_verification"]
        )
        self.assertTrue(
            result["quality"]["browser_order_verification"]["pii_sequence_matched"]
        )

    def test_changed_roster_keeps_downgrade_flags(self):
        existing = make_issue(flags=())
        existing["quality"]["roster_authority"] = "official-issue-page"
        existing["quality"]["browser_order_verification"] = {
            "pii_sequence_matched": True
        }
        refreshed = make_issue(
            dois=("10.1/a", "10.1/c"), flags=("official_order_unverified",)
        )
        result = preserve_existing_content(refreshed, existing)
        self.assertIn("official_order_unverified", result["quality"]["flags"])

    def test_unverified_existing_does_not_clear_flags(self):
        existing = make_issue(flags=())
        refreshed = make_issue(flags=("official_order_unverified",))
        result = preserve_existing_content(refreshed, existing)
        self.assertIn("official_order_unverified", result["quality"]["flags"])


if __name__ == "__main__":
    unittest.main()
