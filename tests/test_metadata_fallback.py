from __future__ import annotations

import unittest

from collectors.metadata_fallback import (
    MetadataFallbackError,
    fetch_crossref_current_issue,
)


def item(title: str, page: str, doi: str, abstract: str = "A complete abstract.") -> dict:
    return {
        "type": "journal-article",
        "volume": "10",
        "issue": "2",
        "title": [title],
        "page": page,
        "DOI": doi,
        "URL": f"https://doi.org/{doi}",
        "abstract": f"<jats:p>{abstract}</jats:p>" if abstract else "",
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "published": {"date-parts": [[2026, 3]]},
    }


class Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class Session:
    def __init__(self, items: list[dict]):
        self.items = items

    def get(self, url: str, **kwargs) -> Response:
        return Response({"message": {"items": self.items}})


class MetadataFallbackTests(unittest.TestCase):
    def test_preserves_page_order_and_excludes_front_matter(self) -> None:
        items = [
            item("Second paper", "20-30", "10.1/second"),
            item("Front Matter", "", "10.1/front", ""),
            item("First paper", "1-19", "10.1/first"),
        ]
        issue = fetch_crossref_current_issue(
            journal_id="test",
            journal_name="Test Journal",
            issn="0000-0000",
            current_issue_url="https://publisher.example/current",
            session=Session(items),
        )
        self.assertEqual(
            [article["title_en"] for article in issue["articles"]],
            ["First paper", "Second paper"],
        )
        self.assertTrue(issue["quality"]["order_preserved"])
        self.assertEqual(issue["quality"]["excluded_item_count"], 1)

    def test_requires_a_usable_issue(self) -> None:
        with self.assertRaises(MetadataFallbackError):
            fetch_crossref_current_issue(
                journal_id="test",
                journal_name="Test Journal",
                issn="0000-0000",
                current_issue_url="https://publisher.example/current",
                session=Session([item("Front Matter", "", "10.1/front", "")]),
            )

    def test_excludes_future_crossref_issue(self) -> None:
        current_items = [
            item("Current first paper", "1-10", "10.1/current-1"),
            item("Current second paper", "11-20", "10.1/current-2"),
        ]
        future_items = []
        for source in current_items:
            future = dict(source)
            future["issue"] = "5"
            future["DOI"] = source["DOI"].replace("current", "future")
            future["published"] = {"date-parts": [[2099, 9]]}
            future_items.append(future)
        issue = fetch_crossref_current_issue(
            journal_id="ecta",
            journal_name="Econometrica",
            issn="0012-9682",
            current_issue_url="https://publisher.example/current",
            session=Session(current_items + future_items),
        )
        self.assertEqual(issue["issue"], "2")
        self.assertEqual(issue["research_article_count"], 2)

    def test_bias_correction_is_not_misclassified(self) -> None:
        items = [
            item("Bias Correction in Dynamic Panels", "1-10", "10.1/bias"),
            item("A Second Research Paper", "11-20", "10.1/second"),
        ]
        issue = fetch_crossref_current_issue(
            journal_id="test",
            journal_name="Test Journal",
            issn="0000-0000",
            current_issue_url="https://publisher.example/current",
            session=Session(items),
        )
        self.assertEqual(issue["research_article_count"], 2)

    def test_missing_page_research_item_fails_roster_match(self) -> None:
        items = [
            item("First paper", "1-10", "10.1/first"),
            item("Second paper", "11-20", "10.1/second"),
            item("Deposited without pages", "", "10.1/incomplete"),
        ]
        issue = fetch_crossref_current_issue(
            journal_id="test",
            journal_name="Test Journal",
            issn="0000-0000",
            current_issue_url="https://publisher.example/current",
            session=Session(items),
        )
        self.assertEqual(issue["expected_article_count"], 3)
        self.assertEqual(issue["research_article_count"], 2)
        self.assertFalse(issue["quality"]["roster_match"])
        self.assertIn("crossref_roster_incomplete", issue["quality"]["flags"])


if __name__ == "__main__":
    unittest.main()
