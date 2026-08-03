from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from collectors.metadata_fallback import (
    MetadataFallbackError,
    _get_content,
    _official_issue_abstracts,
    _publication_date,
    _elsevier_abstract,
    _elsevier_lookup,
    _defer_elsevier_entitlement,
    _sciencedirect_rss_groups,
    _semantic_scholar_metadata_batch,
    fetch_crossref_current_issue,
    fetch_repec_history_issue,
    fetch_sciencedirect_rss_issue,
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
    def __init__(
        self,
        payload: dict,
        content: bytes = b"",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.payload = payload
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class Session:
    def __init__(self, items: list[dict]):
        self.items = items

    def get(self, url: str, **kwargs) -> Response:
        return Response({"message": {"items": self.items}})


class ElsevierSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs) -> Response:
        self.calls.append((url, kwargs))
        return Response(
            {},
            b"""
            <response xmlns:dc="http://purl.org/dc/elements/1.1/">
              <coredata>
                <dc:description>A publisher supplied abstract.</dc:description>
              </coredata>
            </response>
            """,
        )


class ElsevierScopusFallbackSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **kwargs) -> Response:
        self.calls.append(url)
        if "/content/metadata/article" in url or "/content/search/sciencedirect" in url:
            return Response({}, b"<response><coredata /></response>")
        return Response(
            {},
            b"""
            <response xmlns:dc="http://purl.org/dc/elements/1.1/">
              <coredata>
                <dc:description>Abstract retrieved from Scopus.</dc:description>
              </coredata>
            </response>
            """,
        )


class ElsevierSearchLinkSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs) -> Response:
        self.calls.append((url, kwargs))
        if "/content/metadata/article" in url:
            return Response({}, b"<response><coredata /></response>")
        if "/content/search/sciencedirect" in url:
            return Response(
                {},
                b"""
                <response xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
                  <entry>
                    <prism:teaser>Only a teaser, not the full abstract.</prism:teaser>
                    <link ref="abstract"
                      href="http://api.elsevier.com/content/abstract/eid/2-s2.0-123" />
                  </entry>
                </response>
                """,
            )
        if "/content/abstract/eid/" in url:
            return Response(
                {},
                b"""
                <response xmlns:dc="http://purl.org/dc/elements/1.1/">
                  <coredata>
                    <dc:description>Full abstract from the returned link.</dc:description>
                  </coredata>
                </response>
                """,
            )
        return Response({}, b"<response><coredata /></response>")


class ElsevierDeniedSession:
    def get(self, url: str, **kwargs) -> Response:
        return Response({}, b"", status_code=403)


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
    def test_known_entitlement_failure_waits_for_insttoken(self) -> None:
        previous = {
            "sources": {
                "abstract_lookup": {
                    "status": "insufficient_entitlement_missing_insttoken"
                }
            }
        }
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(_defer_elsevier_entitlement(previous))
        with patch.dict(
            "os.environ", {"ELSEVIER_INST_TOKEN": "available"}, clear=True
        ):
            self.assertFalse(_defer_elsevier_entitlement(previous))

    def test_non_elsevier_doi_does_not_call_elsevier_api(self) -> None:
        items = [item("Research paper", "1-20", "10.1111/jofi.70062", "")]
        # A second article keeps the issue above the Crossref roster threshold.
        items.append(item("Research paper two", "21-40", "10.1111/jofi.70063", ""))
        with (
            patch("collectors.metadata_fallback._elsevier_lookup") as lookup,
            patch(
                "collectors.metadata_fallback._openalex_metadata",
                return_value=([], "", ""),
            ),
        ):
            fetch_crossref_current_issue(
                journal_id="jf",
                journal_name="Journal of Finance",
                issn="0022-1082",
                current_issue_url="https://example.org/current",
                session=Session(items),
            )
        lookup.assert_not_called()

    def test_elsevier_abstract_requires_secret_and_never_exposes_it(self) -> None:
        session = ElsevierSession()
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                ("", ""),
                _elsevier_abstract(session, "S0014292126001194", timeout=10),
            )
        self.assertEqual([], session.calls)

        with patch.dict(
            "os.environ",
            {"ELSEVIER_API_KEY": "test-secret", "ELSEVIER_INST_TOKEN": "test-inst-token"},
            clear=True,
        ):
            abstract, source_url = _elsevier_abstract(
                session,
                "S0014-2921(26)00119-4",
                timeout=10,
            )
        self.assertEqual("A publisher supplied abstract.", abstract)
        self.assertEqual(
            "https://api.elsevier.com/content/metadata/article",
            source_url,
        )
        self.assertEqual("test-secret", session.calls[0][1]["headers"]["X-ELS-APIKey"])
        self.assertEqual(
            "test-inst-token",
            session.calls[0][1]["headers"]["X-ELS-Insttoken"],
        )
        self.assertNotIn("test-secret", source_url)
        self.assertEqual(
            'PII("S0014292126001194")',
            session.calls[0][1]["params"]["query"],
        )

    def test_elsevier_abstract_falls_back_to_scopus_retrieval(self) -> None:
        session = ElsevierScopusFallbackSession()
        with patch.dict(
            "os.environ",
            {"ELSEVIER_API_KEY": "test-secret", "ELSEVIER_INST_TOKEN": "test-inst-token"},
            clear=True,
        ):
            abstract, source_url = _elsevier_abstract(
                session,
                "S0014292126001194",
                timeout=10,
            )
        self.assertEqual("Abstract retrieved from Scopus.", abstract)
        self.assertIn("/content/abstract/pii/", source_url)
        self.assertEqual(3, len(session.calls))

    def test_elsevier_lookup_uses_doi_search_and_follows_abstract_link(self) -> None:
        session = ElsevierSearchLinkSession()
        with patch.dict(
            "os.environ",
            {"ELSEVIER_API_KEY": "test-secret", "ELSEVIER_INST_TOKEN": "test-inst-token"},
            clear=True,
        ):
            lookup = _elsevier_lookup(
                session,
                "S0014292126001194",
                doi="10.1016/j.euroecorev.2026.105999",
                timeout=10,
            )
        self.assertEqual(
            "Full abstract from the returned link.",
            lookup["abstract"],
        )
        self.assertEqual(
            "Only a teaser, not the full abstract.",
            lookup["teaser"],
        )
        self.assertEqual("success_full_abstract", lookup["status"])
        self.assertIn("/content/abstract/eid/", lookup["source_url"])
        self.assertEqual(3, len(session.calls))
        self.assertEqual(
            'DOI("10.1016/j.euroecorev.2026.105999")',
            session.calls[0][1]["params"]["query"],
        )

    def test_elsevier_lookup_records_entitlement_failure_without_secret(self) -> None:
        with patch.dict(
            "os.environ",
            {"ELSEVIER_API_KEY": "test-secret", "ELSEVIER_INST_TOKEN": "test-inst-token"},
            clear=True,
        ):
            lookup = _elsevier_lookup(
                ElsevierDeniedSession(),
                "S0014292126001194",
                doi="10.1016/j.euroecorev.2026.105999",
                timeout=10,
            )
        self.assertEqual("", lookup["abstract"])
        self.assertEqual("insufficient_entitlement", lookup["status"])
        self.assertTrue(lookup["attempts"])
        self.assertTrue(
            all(attempt["status_code"] == 403 for attempt in lookup["attempts"])
        )
        self.assertNotIn("test-secret", str(lookup))

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

    def test_excludes_editorial_introductions_from_research_roster(self) -> None:
        issue = fetch_crossref_current_issue(
            journal_id="test",
            journal_name="Test Journal",
            issn="0000-0000",
            current_issue_url="https://publisher.example/current",
            session=Session(
                [
                    item("First paper", "1-10", "10.1/first"),
                    item("Second paper", "11-20", "10.1/second"),
                    item(
                        "Editorial: Introduction to the special issue",
                        "21-22",
                        "10.1/editorial",
                        "",
                    ),
                ]
            ),
        )
        self.assertEqual(2, issue["research_article_count"])
        self.assertEqual(1, issue["quality"]["excluded_item_count"])

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

    def test_sciencedirect_rss_keeps_next_month_but_not_later_volumes(self) -> None:
        feed = b"""
        <rss><channel>
          <item>
            <title>August paper</title>
            <description><![CDATA[
              <p>Publication date: August 2026</p>
              <p><b>Source:</b> Test Journal, Volume 11</p>
              <p>Author(s): Ada Lovelace</p>
            ]]></description>
            <link>https://www.sciencedirect.com/science/article/pii/S000000000000001</link>
          </item>
          <item>
            <title>September paper</title>
            <description><![CDATA[
              <p>Publication date: September 2026</p>
              <p><b>Source:</b> Test Journal, Volume 12</p>
              <p>Author(s): Grace Hopper</p>
            ]]></description>
            <link>https://www.sciencedirect.com/science/article/pii/S000000000000002</link>
          </item>
        </channel></rss>
        """
        groups = _sciencedirect_rss_groups(
            feed,
            lead_months=1,
            today=date(2026, 7, 29),
        )
        self.assertEqual([("11", "August 2026")], [key for key, _items in groups])
        self.assertEqual(["Ada Lovelace"], groups[0][1][0]["authors"])

    def test_sciencedirect_rss_is_the_roster_and_order_authority(self) -> None:
        feed = b"""
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
          <item>
            <title>Official second paper</title>
            <dc:identifier>10.1016/j.demo.2026.002</dc:identifier>
            <description><![CDATA[
              <p>Publication date: August 2026</p>
              <p><b>Source:</b> Demo Journal, Volume 188</p>
              <p>Author(s): Second Author</p>
            ]]></description>
            <link>https://www.sciencedirect.com/science/article/pii/S002</link>
          </item>
          <item>
            <title>Official first paper</title>
            <dc:identifier>10.1016/j.demo.2026.001</dc:identifier>
            <description><![CDATA[
              <p>Publication date: August 2026</p>
              <p><b>Source:</b> Demo Journal, Volume 188</p>
              <p>Author(s): First Author</p>
            ]]></description>
            <link>https://www.sciencedirect.com/science/article/pii/S001</link>
          </item>
        </channel></rss>
        """
        crossref = [
            {
                **item("Official second paper", "2", "10.1016/j.demo.2026.002", "Second abstract."),
                "volume": "188",
                "issue": "",
            },
            {
                **item("Official first paper", "1", "10.1016/j.demo.2026.001", "First abstract."),
                "volume": "188",
                "issue": "",
            },
            {
                **item("Crossref-only extra paper", "3", "10.1016/j.demo.2026.003", "Extra abstract."),
                "volume": "188",
                "issue": "",
            },
        ]

        class RssSession:
            def get(self, url: str, **kwargs) -> Response:
                if "rss.sciencedirect.com" in url:
                    return Response({}, feed)
                return Response({"message": {"items": crossref}})

        issue = fetch_sciencedirect_rss_issue(
            journal_id="demo",
            journal_name="Demo Journal",
            issn="0000-0000",
            current_issue_url="https://example.org/issues",
            issue_url_template="https://example.org/vol/{volume}/suppl/{issue}",
            rss_url="https://rss.sciencedirect.com/demo",
            session=RssSession(),
            today=date(2026, 7, 30),
        )
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertEqual(
            ["Official first paper", "Official second paper"],
            [article["title_en"] for article in issue["articles"]],
        )
        self.assertEqual([1, 2], [article["sequence"] for article in issue["articles"]])
        self.assertTrue(issue["quality"]["roster_match"])
        self.assertTrue(issue["quality"]["order_preserved"])
        self.assertIn(
            "publisher_rss_reverse_order_normalized",
            issue["quality"]["flags"],
        )

        self.assertEqual("publisher-rss", issue["quality"]["roster_authority"])
        self.assertNotIn("publisher_rss_roster_mismatch", issue["quality"]["flags"])

    def test_sciencedirect_self_heal_preserves_existing_abstract(self) -> None:
        feed = b"""
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <title>Existing paper</title>
          <dc:identifier>10.1016/j.demo.2026.010</dc:identifier>
          <description><![CDATA[
            <p>Publication date: August 2026</p>
            <p><b>Source:</b> Demo Journal, Volume 188</p>
            <p>Author(s): Existing Author</p>
          ]]></description>
          <link>https://www.sciencedirect.com/science/article/pii/S010</link>
        </item></channel></rss>
        """

        class RssSession:
            def get(self, url: str, **kwargs) -> Response:
                if "rss.sciencedirect.com" in url:
                    return Response({}, feed)
                return Response(
                    {"message": {"items": [{
                        **item("Existing paper", "1", "10.1016/j.demo.2026.010", ""),
                        "volume": "188",
                        "issue": "",
                    }]}}
                )

        existing = {
            "issue_id": "demo-188-c",
            "articles": [{
                "doi": "10.1016/j.demo.2026.010",
                "title_en": "Existing paper",
                "title_cn": "既有论文",
                "authors": ["Existing Author"],
                "abstract_en": "Previously recovered abstract.",
                "abstract_cn": "此前已经完成的摘要。",
                "source_url": "https://www.sciencedirect.com/science/article/pii/S010",
                "sources": {"abstract_en": "openalex"},
                "translation": {"status": "complete"},
            }],
        }
        with (
            patch("collectors.metadata_fallback._elsevier_lookup") as elsevier,
            patch("collectors.metadata_fallback._openalex_metadata") as openalex,
            patch("collectors.metadata_fallback._semantic_scholar_metadata_batch") as semantic,
        ):
            issue = fetch_sciencedirect_rss_issue(
                journal_id="demo",
                journal_name="Demo Journal",
                issn="0000-0000",
                current_issue_url="https://example.org/issues",
                issue_url_template="https://example.org/vol/{volume}/suppl/{issue}",
                rss_url="https://rss.sciencedirect.com/demo",
                session=RssSession(),
                today=date(2026, 7, 30),
                existing_issue=existing,
            )
        assert issue is not None
        self.assertEqual("Previously recovered abstract.", issue["articles"][0]["abstract_en"])
        self.assertEqual("既有论文", issue["articles"][0]["title_cn"])
        self.assertEqual("此前已经完成的摘要。", issue["articles"][0]["abstract_cn"])
        elsevier.assert_not_called()
        openalex.assert_not_called()
        semantic.assert_not_called()

    def test_force_elsevier_replaces_fallback_abstract_but_keeps_roster(self) -> None:
        feed = b"""
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <title>Existing paper</title>
          <dc:identifier>10.1016/j.demo.2026.011</dc:identifier>
          <description><![CDATA[
            <p>Publication date: August 2026</p>
            <p><b>Source:</b> Demo Journal, Volume 188</p>
            <p>Author(s): Existing Author</p>
          ]]></description>
          <link>https://www.sciencedirect.com/science/article/pii/S010</link>
        </item></channel></rss>
        """

        class RssSession:
            def get(self, url: str, **kwargs) -> Response:
                if "rss.sciencedirect.com" in url:
                    return Response({}, feed)
                return Response(
                    {"message": {"items": [{
                        **item("Existing paper", "1", "10.1016/j.demo.2026.011", ""),
                        "volume": "188",
                        "issue": "",
                    }]}}
                )

        existing = {
            "issue_id": "demo-188-c",
            "articles": [{
                "doi": "10.1016/j.demo.2026.011",
                "title_en": "Existing paper",
                "title_cn": "既有论文",
                "authors": ["Existing Author"],
                "abstract_en": "OpenAlex fallback abstract.",
                "abstract_cn": "此前已经完成的摘要。",
                "source_url": "https://www.sciencedirect.com/science/article/pii/S010",
                "sources": {"abstract_en": "openalex"},
                "translation": {"status": "complete"},
            }],
        }
        with (
            patch("collectors.metadata_fallback._elsevier_lookup") as elsevier,
            patch("collectors.metadata_fallback._openalex_metadata") as openalex,
            patch("collectors.metadata_fallback._semantic_scholar_metadata_batch") as semantic,
        ):
            elsevier.return_value = {
                "abstract": "Publisher supplied abstract.",
                "teaser": "",
                "source": "elsevier-article-metadata",
                "status": "success_full_abstract",
                "attempts": [],
            }
            issue = fetch_sciencedirect_rss_issue(
                journal_id="demo",
                journal_name="Demo Journal",
                issn="0000-0000",
                current_issue_url="https://example.org/issues",
                issue_url_template="https://example.org/vol/{volume}/suppl/{issue}",
                rss_url="https://rss.sciencedirect.com/demo",
                session=RssSession(),
                today=date(2026, 7, 30),
                existing_issue=existing,
                force_elsevier=True,
            )
        assert issue is not None
        article = issue["articles"][0]
        self.assertEqual("Publisher supplied abstract.", article["abstract_en"])
        self.assertEqual(
            "elsevier-article-metadata",
            article["sources"]["abstract_en"],
        )
        elsevier.assert_called_once()
        openalex.assert_not_called()
        semantic.assert_not_called()

    def test_force_elsevier_failed_lookup_keeps_existing_abstract(self) -> None:
        feed = b"""
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <title>Existing paper</title>
          <dc:identifier>10.1016/j.demo.2026.012</dc:identifier>
          <description><![CDATA[
            <p>Publication date: August 2026</p>
            <p><b>Source:</b> Demo Journal, Volume 188</p>
            <p>Author(s): Existing Author</p>
          ]]></description>
          <link>https://www.sciencedirect.com/science/article/pii/S010</link>
        </item></channel></rss>
        """

        class RssSession:
            def get(self, url: str, **kwargs) -> Response:
                if "rss.sciencedirect.com" in url:
                    return Response({}, feed)
                return Response(
                    {"message": {"items": [{
                        **item("Existing paper", "1", "10.1016/j.demo.2026.012", ""),
                        "volume": "188",
                        "issue": "",
                    }]}}
                )

        existing = {
            "issue_id": "demo-188-c",
            "articles": [{
                "doi": "10.1016/j.demo.2026.012",
                "title_en": "Existing paper",
                "title_cn": "既有论文",
                "authors": ["Existing Author"],
                "abstract_en": "Previously recovered abstract.",
                "abstract_cn": "此前已经完成的摘要。",
                "source_url": "https://www.sciencedirect.com/science/article/pii/S010",
                "sources": {"abstract_en": "openalex"},
                "translation": {"status": "complete"},
            }],
        }
        with (
            patch("collectors.metadata_fallback._elsevier_lookup") as elsevier,
            patch("collectors.metadata_fallback._openalex_metadata") as openalex,
            patch("collectors.metadata_fallback._semantic_scholar_metadata_batch") as semantic,
        ):
            elsevier.return_value = {
                "abstract": "",
                "teaser": "A teaser must never pass as the abstract.",
                "source": "",
                "status": "success_teaser_only",
                "attempts": [],
            }
            issue = fetch_sciencedirect_rss_issue(
                journal_id="demo",
                journal_name="Demo Journal",
                issn="0000-0000",
                current_issue_url="https://example.org/issues",
                issue_url_template="https://example.org/vol/{volume}/suppl/{issue}",
                rss_url="https://rss.sciencedirect.com/demo",
                session=RssSession(),
                today=date(2026, 7, 30),
                existing_issue=existing,
                force_elsevier=True,
            )
        assert issue is not None
        article = issue["articles"][0]
        self.assertEqual("Previously recovered abstract.", article["abstract_en"])
        self.assertEqual("openalex", article["sources"]["abstract_en"])
        self.assertNotIn("abstract_en_missing", article["quality_flags"])

    def test_crossref_blank_issue_can_publish_as_continuous_volume(self) -> None:
        items = [
            {
                **item("August first paper", "105701", "10.1/august-1"),
                "volume": "11",
                "issue": "",
                "published": {"date-parts": [[2026, 8, 1]]},
            },
            {
                **item("August second paper", "105702", "10.1/august-2"),
                "volume": "11",
                "issue": "",
                "published": {"date-parts": [[2026, 8, 1]]},
            },
        ]
        issue = fetch_crossref_current_issue(
            journal_id="test",
            journal_name="Test Journal",
            issn="0000-0000",
            current_issue_url="https://publisher.example/vol/11",
            target_volume="11",
            target_issue="",
            output_issue="C",
            future_cutoff=datetime(2026, 8, 31, tzinfo=timezone.utc),
            items_override=items,
            session=Session(items),
        )
        self.assertEqual("test-11-c", issue["issue_id"])
        self.assertEqual("C", issue["issue"])
        self.assertEqual(2, issue["research_article_count"])

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


    def test_semantic_scholar_null_abstract_is_not_treated_as_content(self) -> None:
        class SemanticSession:
            def post(self, url: str, **kwargs) -> Response:
                return Response([
                    {
                        "authors": [{"name": "Ada Lovelace"}],
                        "abstract": None,
                        "url": "https://www.semanticscholar.org/paper/demo",
                        "externalIds": {"DOI": "10.1/DEMO"},
                    }
                ])

        result = _semantic_scholar_metadata_batch(
            SemanticSession(), ["10.1/demo"], timeout=12
        )
        self.assertEqual("", result["10.1/demo"]["abstract"])


    def test_semantic_scholar_fallback_uses_one_bounded_batch(self) -> None:
        class SemanticSession:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def post(self, url: str, **kwargs) -> Response:
                self.calls.append((url, kwargs))
                return Response([
                    {
                        "authors": [{"name": "Ada Lovelace"}],
                        "abstract": "A public fallback abstract.",
                        "url": "https://www.semanticscholar.org/paper/demo",
                        "externalIds": {"DOI": "10.1/DEMO"},
                    }
                ])

        session = SemanticSession()
        result = _semantic_scholar_metadata_batch(
            session, ["10.1/demo", "10.1/demo"], timeout=12
        )
        self.assertEqual(1, len(session.calls))
        _, request = session.calls[0]
        self.assertEqual({"ids": ["DOI:10.1/demo"]}, request["json"])
        self.assertEqual(12, request["timeout"])
        self.assertEqual(["Ada Lovelace"], result["10.1/demo"]["authors"])
        self.assertEqual(
            "A public fallback abstract.", result["10.1/demo"]["abstract"]
        )


    def test_sciencedirect_rss_audits_corrections_and_editorial_material(self) -> None:
        feed = b"""
        <rss><channel>
          <item><title>Research paper</title><description>
            Publication date: August 2026 Source: Demo, Volume 12 Author(s): Ada
          </description><link>https://www.sciencedirect.com/science/article/pii/S001</link></item>
          <item><title>Corrigendum to Research paper</title><description>
            Publication date: August 2026 Source: Demo, Volume 12 Author(s): Ada
          </description><link>https://www.sciencedirect.com/science/article/pii/S002</link></item>
          <item><title>Editorial Board</title><description>
            Publication date: August 2026 Source: Demo, Volume 12 Author(s):
          </description><link>https://www.sciencedirect.com/science/article/pii/S003</link></item>
        </channel></rss>
        """

        class RssSession:
            def get(self, url: str, **kwargs) -> Response:
                if "rss.sciencedirect.com" in url:
                    return Response({}, feed)
                return Response({"message": {"items": []}})

        issue = fetch_sciencedirect_rss_issue(
            journal_id="demo",
            journal_name="Demo Journal",
            issn="0000-0000",
            current_issue_url="https://example.org/issues",
            issue_url_template="https://example.org/vol/{volume}/suppl/{issue}",
            rss_url="https://rss.sciencedirect.com/demo",
            session=RssSession(),
            today=date(2026, 7, 30),
        )
        assert issue is not None
        self.assertEqual(1, issue["research_article_count"])
        self.assertEqual(3, issue["quality"]["official_item_count"])
        self.assertEqual(2, issue["quality"]["excluded_item_count"])
        self.assertEqual(
            {"correction", "editorial"},
            {item["article_type"] for item in issue["quality"]["excluded_items"]},
        )
        self.assertIn("official_order_unverified", issue["quality"]["flags"])


    def test_ere_issue_months_use_official_calendar(self) -> None:
        from collectors.metadata_fallback import MONTHS_BY_ISSUE
        items = [
            {
                "type": "journal-article",
                "volume": "89",
                "issue": "8",
                "title": ["A paper"],
                "DOI": "10.1007/demo",
                "author": [{"given": "Ada", "family": "Lovelace"}],
                "published": {"date-parts": [[2026, 4]]},
            }
        ]
        self.assertEqual(
            "August 2026",
            _publication_date("0924-6460", "89", "8", items),
        )
        self.assertEqual("May", MONTHS_BY_ISSUE["0924-6460"]["5"])
        self.assertEqual("June", MONTHS_BY_ISSUE["0924-6460"]["6"])
        self.assertEqual("July", MONTHS_BY_ISSUE["0924-6460"]["7"])

    def test_get_content_patient_403_retries_then_succeeds(self) -> None:
        import requests

        class PatienceSession:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, url: str, **kwargs) -> Response:
                self.calls += 1
                if self.calls == 1:
                    error = requests.HTTPError("forbidden")
                    error.response = SimpleNamespace(status_code=403)
                    raise error
                return Response({}, b"ok")

        session = PatienceSession()
        with patch("time.sleep") as sleep:
            content = _get_content(session, "https://example.org/issue", attempts=3, patient_403=True)
        self.assertEqual(b"ok", content)
        self.assertEqual(2, session.calls)
        sleep.assert_called_once()

    def test_official_issue_abstracts_extracts_pii_map(self) -> None:
        html = b"""
        <ol>
          <li class="js-article-list-item">
            <a href="https://www.sciencedirect.com/science/article/pii/S123">
              <span class="js-article-title">First paper</span>
            </a>
            <div class="js-abstract-body-text"><p>First official abstract.</p></div>
          </li>
          <li class="js-article-list-item">
            <a href="https://www.sciencedirect.com/science/article/pii/S456">
              <span class="js-article-title">Second paper</span>
            </a>
            <div class="js-abstract-body-text"><p>Second official abstract.</p></div>
          </li>
        </ol>
        """
        abstracts = _official_issue_abstracts(html)
        self.assertEqual(
            {"S123": "First official abstract.", "S456": "Second official abstract."},
            abstracts,
        )

    def test_sciencedirect_rss_uses_official_html_when_abstract_missing(self) -> None:
        feed = b"""
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <title>Missing abstract paper</title>
          <dc:identifier>10.1016/j.demo.2026.020</dc:identifier>
          <description><![CDATA[
            <p>Publication date: August 2026</p>
            <p><b>Source:</b> Demo Journal, Volume 188</p>
            <p>Author(s): Ada Lovelace</p>
          ]]></description>
          <link>https://www.sciencedirect.com/science/article/pii/S789</link>
        </item></channel></rss>
        """
        official_html = b"""
        <ol>
          <li class="js-article-list-item">
            <a href="https://www.sciencedirect.com/science/article/pii/S789">
              <span class="js-article-title">Missing abstract paper</span>
            </a>
            <div class="js-abstract-body-text"><p>Recovered from official page.</p></div>
          </li>
        </ol>
        """

        class RssSession:
            def get(self, url: str, **kwargs) -> Response:
                if "rss.sciencedirect.com" in url:
                    return Response({}, feed)
                if "example.org/vol/188" in url:
                    return Response({}, official_html)
                return Response(
                    {"message": {"items": [{
                        **item("Missing abstract paper", "1", "10.1016/j.demo.2026.020", ""),
                        "volume": "188",
                        "issue": "",
                    }]}}
                )

        with (
            patch("collectors.metadata_fallback._elsevier_lookup",
                  return_value={"abstract": "", "teaser": "", "source_url": "", "source": "", "status": "not_found", "attempts": []}) as elsevier,
            patch("collectors.metadata_fallback._openalex_metadata",
                  return_value=([], "", "")) as openalex,
            patch("collectors.metadata_fallback._semantic_scholar_metadata_batch",
                  return_value={}) as semantic,
        ):
            issue = fetch_sciencedirect_rss_issue(
                journal_id="demo",
                journal_name="Demo Journal",
                issn="0000-0000",
                current_issue_url="https://example.org/issues",
                issue_url_template="https://example.org/vol/{volume}/suppl/{issue}",
                rss_url="https://rss.sciencedirect.com/demo",
                session=RssSession(),
                today=date(2026, 7, 30),
            )
        assert issue is not None
        article = issue["articles"][0]
        self.assertEqual("Recovered from official page.", article["abstract_en"])
        self.assertEqual("official-sciencedirect-issue", article["sources"]["abstract_en"])
        self.assertNotIn("abstract_en_missing", article["quality_flags"])


class ElsevierRateLimitSession:
    def __init__(self, remaining: int = 19500) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.remaining = remaining

    def get(self, url: str, **kwargs) -> Response:
        self.calls.append((url, kwargs))
        return Response(
            {},
            b"""
            <response xmlns:dc="http://purl.org/dc/elements/1.1/">
              <coredata>
                <dc:description>A publisher supplied abstract.</dc:description>
              </coredata>
            </response>
            """,
            headers={
                "X-RateLimit-Limit": "20000",
                "X-RateLimit-Remaining": str(self.remaining),
                "X-RateLimit-Reset": "1783457891",
                "X-ELS-Status": "OK",
            },
        )


class ElsevierQuotaTest(unittest.TestCase):
    def test_lookup_records_rate_limit_headers(self) -> None:
        session = ElsevierRateLimitSession(remaining=19500)
        with patch.dict(
            "os.environ",
            {"ELSEVIER_API_KEY": "test-secret", "ELSEVIER_INST_TOKEN": "test-inst-token"},
            clear=True,
        ):
            lookup = _elsevier_lookup(
                session, "S0014292126001194", timeout=10
            )
        self.assertEqual(
            {
                "limit": 20000,
                "remaining": 19500,
                "resets_at": "2026-07-07T20:58:11+00:00",
                "els_status": "OK",
            },
            lookup["rate_limit"],
        )
        self.assertEqual(
            {"limit": 20000, "remaining": 19500},
            {
                key: value
                for key, value in lookup["attempts"][0]["rate_limit"].items()
                if key in {"limit", "remaining"}
            },
        )
        self.assertEqual("", lookup["quota_warning"])

    def test_lookup_warns_when_quota_is_nearly_exhausted(self) -> None:
        session = ElsevierRateLimitSession(remaining=1500)
        with patch.dict(
            "os.environ",
            {"ELSEVIER_API_KEY": "test-secret", "ELSEVIER_INST_TOKEN": "test-inst-token"},
            clear=True,
        ):
            lookup = _elsevier_lookup(
                session, "S0014292126001194", timeout=10
            )
        self.assertIn("quota nearly exhausted", lookup["quota_warning"])
        self.assertIn("1500/20000", lookup["quota_warning"])


if __name__ == "__main__":
    unittest.main()
