from __future__ import annotations

import unittest
from unittest.mock import patch

from collectors.history import HistoricalIssue
from collectors.metadata_fallback import _configured_repec_doi, fetch_repec_history_issue
from scripts.backfill_history import collector_for_issue


class Response:
    def __init__(self, payload: dict | None = None, content: bytes = b"", status_code: int = 200) -> None:
        self.payload = payload or {}
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class TemplateRepecSession:
    def get(self, url: str, **kwargs) -> Response:
        if "api.crossref.org" in url:
            return Response({"message": {"items": []}})
        if "/s/the/publsh.html" in url:
            return Response(
                content=b"""
                <html><body>
                  <h3>2023, Volume 18, Issue 1</h3>
                  <ul>
                    <li><a href="/a/the/publsh/3501.html">Sample TE paper</a></li>
                  </ul>
                </body></html>
                """
            )
        if "/a/the/publsh/3501.html" in url:
            return Response(
                content=b"""
                <html><body>
                  <h2>Author</h2><ul><li>Ada Lovelace</li></ul>
                  <h2>Abstract</h2><p>A complete publisher-supplied abstract.</p>
                </body></html>
                """
            )
        return Response({"authorships": []})


class RepecDoiTemplateTests(unittest.TestCase):
    def test_configured_template_derives_doi_from_numeric_repec_handle(self) -> None:
        self.assertEqual(
            "10.3982/te3501",
            _configured_repec_doi(
                "https://ideas.repec.org/a/the/publsh/3501.html",
                "10.3982/TE{id}",
            ),
        )

    def test_missing_template_never_derives_doi(self) -> None:
        self.assertEqual(
            "",
            _configured_repec_doi(
                "https://ideas.repec.org/a/the/publsh/3501.html",
                "",
            ),
        )

    def test_non_numeric_or_non_repec_handle_never_derives_doi(self) -> None:
        self.assertEqual(
            "",
            _configured_repec_doi(
                "https://ideas.repec.org/a/the/publsh/article-x.html",
                "10.3982/TE{id}",
            ),
        )
        self.assertEqual(
            "",
            _configured_repec_doi(
                "https://example.org/a/the/publsh/3501.html",
                "10.3982/TE{id}",
            ),
        )

    def test_fetch_repec_history_issue_uses_configured_template_for_article_identity(self) -> None:
        with patch(
            "collectors.metadata_fallback._openalex_metadata",
            return_value=([], "", ""),
        ):
            issue = fetch_repec_history_issue(
                journal_id="te",
                journal_name="Theoretical Economics",
                issn="1555-7561",
                volume="18",
                issue="1",
                repec_series_code="the/publsh",
                doi_template="10.3982/TE{id}",
                session=TemplateRepecSession(),
            )
        article = issue["articles"][0]
        self.assertEqual("10.3982/te3501", article["doi"])
        self.assertEqual("doi:10.3982/te3501", article["paper_id"])
        self.assertEqual("https://doi.org/10.3982/te3501", article["source_url"])

    def test_repec_backfill_passes_only_explicit_configured_template(self) -> None:
        config = {
            "id": "te",
            "name": "Theoretical Economics",
            "collector": "repec",
            "issn": "1555-7561",
            "repec_series_code": "the/publsh",
            "doi_template": "10.3982/TE{id}",
        }
        ref = HistoricalIssue(
            "TE",
            2023,
            "18",
            "1",
            "https://ideas.repec.org/s/the/publsh.html",
        )
        with patch(
            "collectors.metadata_fallback.fetch_repec_history_issue",
            return_value={"issue_id": "te-18-1"},
        ) as fetch:
            result = collector_for_issue(config, ref)()
        self.assertEqual("te-18-1", result["issue_id"])
        self.assertEqual("10.3982/TE{id}", fetch.call_args.kwargs["doi_template"])

        config_without_template = dict(config)
        config_without_template.pop("doi_template")
        with patch(
            "collectors.metadata_fallback.fetch_repec_history_issue",
            return_value={"issue_id": "te-18-1"},
        ) as fetch_without_template:
            collector_for_issue(config_without_template, ref)()
        self.assertNotIn("doi_template", fetch_without_template.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
