from __future__ import annotations

import unittest

from collectors.metadata_fallback import (
    MetadataFallbackError,
    fetch_crossref_current_issue,
    fetch_repec_history_issue,
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
    def __init__(self, payload: dict, content: bytes = b""):
        self.payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class Session:
    def __init__(self, items: list[dict]):
        self.items = items

    def get(self, url: str, **kwargs) -> Response:
        return Response({"message": {"items": self.items}})


class RepecHistorySession:
    def __init__(self) -> None:
        self.items = [
            item("First JPE paper", "1-10", "10.1086/740001", ""),
            item("Second JPE paper", "11-20", "10.1086/740002", ""),
        ]

    def get(self, url: str, **kwargs) -> Response:
        if "api.crossref.org" in url:
            return Response({"message": {"items": self.items}})
        if "/s/ucp/jpolec.html" in url:
            return Response(
                {},
                b"""
                <html><body>
                  <h3>2025, Volume 133, Issue 1</h3>
                  <ul>
                    <li><a href="/a/ucp/jpolec/doi10.1086-740001.html">First JPE paper</a></li>
                    <li><a href="/a/ucp/jpolec/doi10.1086-740002.html">Second JPE paper</a></li>
                  </ul>
                </body></html>
                """,
            )
        if "/a/ucp/jpolec/doi10.1086-740001.html" in url:
            return Response({}, b"<html><h2>Abstract</h2><p>First abstract from RePEc.</p></html>")
        if "/a/ucp/jpolec/doi10.1086-740002.html" in url:
            return Response({}, b"<html><h2>Abstract</h2><p>Second abstract from RePEc.</p></html>")
        return Response({"authorships": []})


class RepecHistoryNoAbstractSession:
    def __init__(self) -> None:
        self.items = [item("Robert E. Lucas Jr.: Supreme among Macroeconomists as a Bird Who Saw Further than Others", "1-10", "10.1086/737998", "")]

    def get(self, url: str, **kwargs) -> Response:
        if "api.crossref.org" in url:
            return Response({"message": {"items": self.items}})
        if "/s/ucp/jpolec.html" in url:
            return Response(
                {},
                b"""
                <html><body>
                  <h3>2025, Volume 133, Issue 11</h3>
                  <ul>
                    <li><a href="/a/ucp/jpolec/doi10.1086-737998.html">Robert E. Lucas Jr.: Supreme among Macroeconomists as a Bird Who Saw Further than Others</a></li>
                  </ul>
                </body></html>
                """,
            )
        if "/a/ucp/jpolec/doi10.1086-737998.html" in url:
            return Response({}, b"<html><h2>Abstract</h2><p>No abstract is available for this item.</p></html>")
        return Response({"authorships": []})


class RepecEconometricaSession:
    def __init__(self) -> None:
        self.items = [
            item("Mussa Puzzle Redux", "1-39", "10.3982/ecta20849", "")
        ]

    def get(self, url: str, **kwargs) -> Response:
        if "api.crossref.org" in url:
            return Response({"message": {"items": self.items}})
        if "/s/wly/emetrp.html" in url:
            return Response(
                {},
                b"""
                <html><body>
                  <h3>January 2025, Volume 93, Issue 1</h3>
                  <ul>
                    <li><a href="/a/wly/emetrp/v93y2025i1p1-39.html">Mussa Puzzle Redux</a></li>
                  </ul>
                </body></html>
                """,
            )
        if "/a/wly/emetrp/v93y2025i1p1-39.html" in url:
            return Response(
                {},
                b"""
                <html><body>
                  <h2>Author</h2><ul><li>Oleg Itskhoki</li><li>Dmitry Mukhin</li></ul>
                  <h2>Abstract</h2><p>A publisher-supplied abstract.</p>
                  <h2>Suggested Citation</h2><p>DOI: 10.3982/ECTA20849</p>
                </body></html>
                """,
            )
        return Response({"authorships": []})


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

    def test_keeps_scholarly_comments_in_issue_contents(self) -> None:
        items = [
            item("The Main Research Paper", "1-10", "10.1/main"),
            item("The Origin of the State: Land Productivity or Appropriability? A Comment", "11-20", "10.1/comment"),
        ]
        issue = fetch_crossref_current_issue(
            journal_id="jpe",
            journal_name="Journal of Political Economy",
            issn="0022-3808",
            current_issue_url="https://publisher.example/current",
            session=Session(items),
        )
        self.assertEqual(issue["research_article_count"], 2)
        self.assertEqual(issue["articles"][1]["article_type"], "comment")
        self.assertEqual(issue["quality"]["excluded_item_count"], 0)

    def test_keeps_page_less_research_item_with_stable_doi_locator(self) -> None:
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
        self.assertEqual(issue["research_article_count"], 3)
        self.assertTrue(issue["quality"]["roster_match"])
        self.assertNotIn("crossref_roster_incomplete", issue["quality"]["flags"])

    def test_builds_jpe_history_from_repec_issue_section(self) -> None:
        issue = fetch_repec_history_issue(
            journal_id="jpe",
            journal_name="Journal of Political Economy",
            issn="0022-3808",
            volume="133",
            issue="1",
            repec_series_code="ucp/jpolec",
            session=RepecHistorySession(),
        )
        self.assertEqual(issue["issue_id"], "jpe-133-1")
        self.assertEqual(
            [article["title_en"] for article in issue["articles"]],
            ["First JPE paper", "Second JPE paper"],
        )
        self.assertEqual(issue["articles"][0]["abstract_en"], "First abstract from RePEc.")
        self.assertEqual(issue["quality"]["roster_transport"], "repec-serial-page")
        self.assertTrue(issue["quality"]["order_preserved"])

    def test_treats_no_abstract_repec_commentary_as_comment(self) -> None:
        issue = fetch_repec_history_issue(
            journal_id="jpe",
            journal_name="Journal of Political Economy",
            issn="0022-3808",
            volume="133",
            issue="11",
            repec_series_code="ucp/jpolec",
            session=RepecHistoryNoAbstractSession(),
        )
        self.assertEqual(issue["articles"][0]["article_type"], "comment")
        self.assertEqual(issue["articles"][0]["abstract_en"], "")
        self.assertNotIn("abstract_en_incomplete", issue["quality"]["flags"])

    def test_enriches_repec_entries_when_serial_link_has_no_doi(self) -> None:
        issue = fetch_repec_history_issue(
            journal_id="ecta",
            journal_name="Econometrica",
            issn="0012-9682",
            volume="93",
            issue="1",
            repec_series_code="wly/emetrp",
            session=RepecEconometricaSession(),
        )
        article = issue["articles"][0]
        self.assertEqual("10.3982/ecta20849", article["doi"])
        self.assertEqual(["Oleg Itskhoki", "Dmitry Mukhin"], article["authors"])
        self.assertEqual("A publisher-supplied abstract.", article["abstract_en"])
        self.assertNotIn("doi_missing", article["quality_flags"])


if __name__ == "__main__":
    unittest.main()
