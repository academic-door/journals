"""Offline first-party current-issue issue-existence freshness regressions.

Publisher issue headers may trigger investigation when Crossref misses an
issue, but MUST NOT fabricate official full TOCs, DOIs or abstract rights.
"""
from __future__ import annotations

import copy
import requests
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.journal_monitor import (
    _first_party_issue_candidate,
    _publisher_probe_failure_details,
    _request_with_retry,
    _trusted_issue_signal,
    detect_all,
    fetch_official_issue_signal,
    public_status,
)

SOURCES = {
    "ej": ("oup_current_issue", "https://academic.oup.com/ej/issue",
           "Volume 136, Issue 679, October 2026", "136", "679", "October 2026"),
    "rfs": ("oup_current_issue", "https://academic.oup.com/rfs/issue",
            "Volume 39, Issue 11, November 2026", "39", "11", "November 2026"),
    "jle": ("chicago_current_issue",
            "https://www.journals.uchicago.edu/toc/jole/current",
            "Volume 44, Number 4 October 2026", "44", "4", "2026-10"),
}


def baseline(jid: str, volume: str, issue: str) -> dict:
    return {
        "journal_id": jid,
        "issue_id": f"{jid}-{volume}-{issue}",
        "volume": volume,
        "issue": issue,
        "publication_date": "August 2026",
        "articles": [{"doi": "10.1234/old", "authors": ["A"],
                      "abstract_en": "Prior verified abstract"}],
    }


class PublisherCurrentIssueSignalsTests(unittest.TestCase):
    def signal(self, jid: str) -> dict:
        kind, url, heading, volume, issue, publication = SOURCES[jid]
        with patch(
            "scripts.journal_monitor._request_with_retry",
            return_value=SimpleNamespace(
                url=url,
                content=f"<html><head><title>{heading}</title></head>"
                        f"<body><main><h1>{heading}</h1></main></body></html>".encode(),
            ),
        ):
            result = fetch_official_issue_signal({
                "id": jid, "announcement_source": kind, "announcement_url": url,
            })
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual("official_current_issue", result["source_kind"])
        self.assertEqual((volume, issue, publication),
                         (result["volume"], result["issue"], result["publication_date"]))
        self.assertTrue(_trusted_issue_signal(result))
        return result

    def test_all_three_publisher_issue_headers_parse(self):
        for jid in SOURCES:
            with self.subTest(jid=jid):
                self.signal(jid)

    def test_crossref_empty_still_confirms_publisher_newer_issue_only(self):
        for jid, old, prior in [
            ("ej", "136", "678"), ("rfs", "39", "10"), ("jle", "44", "3")
        ]:
            with self.subTest(jid=jid):
                signal = self.signal(jid)
                config = {
                    jid.upper(): {
                        "id": jid, "enabled": True, "issn": "0000-0000",
                        "announcement_source": SOURCES[jid][0],
                        "announcement_url": SOURCES[jid][1],
                    }
                }
                with patch("scripts.journal_monitor.read_json",
                           return_value=baseline(jid, old, prior)):
                    next_state, result = detect_all(
                        config, {"journals": {}},
                        crossref_fetcher=lambda _config, _baseline: [],
                        issue_signal_fetcher=lambda _config: signal,
                    )
                key = jid.upper()
                self.assertEqual([key], result["confirmed_journals"])
                candidate = next_state["journals"][key]["candidate"]
                self.assertEqual(0, candidate["doi_count"])
                self.assertEqual([], candidate["unseen_dois"])
                self.assertIn("official_current_issue", next_state["journals"][key]["evidence"])
                announcement = next_state["journals"][key]["announcement"]
                self.assertEqual(f"{jid}-{signal['volume']}-{signal['issue']}",
                                 announcement["issue_id"])
                self.assertEqual("first_party", announcement["source_authority"])
                self.assertEqual("official_current_issue", announcement["source_kind"])
                self.assertEqual("announced", announcement["publication_state"])
                self.assertEqual("August 2026", baseline(jid, old, prior)["publication_date"])
                source_probe = next_state["journals"][key]["publisher_current_issue_signal"]
                self.assertEqual("issue_header_observed", source_probe["status"])
                self.assertEqual(f"{jid}-{signal['volume']}-{signal['issue']}", source_probe["publisher_issue_id"])
                self.assertEqual("issue_header_only_not_full_roster", source_probe["scope"])
                exposed = public_status(next_state, result)
                self.assertEqual(0, exposed["summary"]["publisher_issue_signal_blocked"])
                self.assertEqual("issue_header_observed",
                                 exposed["publisher_current_issue_signals"][key]["status"])

    def test_first_party_403_is_explicitly_blocked_not_verified_parity(self):
        for jid, vol, issue in [("ej", "136", "678"),
                                 ("rfs", "39", "10"),
                                 ("jle", "44", "3")]:
            with self.subTest(jid=jid):
                kind, url, *_ = SOURCES[jid]
                config = {jid.upper(): {
                    "id": jid, "enabled": True, "issn": "0000-0000",
                    "announcement_source": kind, "announcement_url": url,
                }}
                def denied(_config):
                    raise RuntimeError("publisher HTTP 403")
                with patch("scripts.journal_monitor.read_json",
                           return_value=baseline(jid, vol, issue)):
                    state, result = detect_all(
                        config, {"journals": {}},
                        crossref_fetcher=lambda _config, _baseline: [],
                        issue_signal_fetcher=denied,
                    )
                entry = state["journals"][jid.upper()]
                self.assertEqual("unchanged", entry["status"])
                self.assertIsNone(entry["candidate"])
                source = entry["publisher_current_issue_signal"]
                self.assertEqual("verification_blocked", source["status"])
                self.assertEqual("RuntimeError", source["failure_class"])
                self.assertEqual(url, source["source_url"])
                self.assertEqual("issue_header_only_not_full_roster", source["scope"])
                self.assertNotIn("publisher_issue_id", source)
                self.assertNotIn("publisher HTTP 403", str(source))
                report = public_status(state, result)
                self.assertEqual(1, report["summary"]["publisher_issue_signal_blocked"])
                self.assertEqual("healthy", report["status"])
                self.assertEqual("verification_blocked",
                                 report["publisher_current_issue_signals"][jid.upper()]["status"])

    def test_metadata_request_preserves_typed_http_failure_cause(self):
        response = requests.Response()
        response.status_code = 403
        response.url = SOURCES["ej"][1]
        error = requests.HTTPError(
            "secret-bearing transport message must not appear in public data",
            response=response,
        )
        session = SimpleNamespace(get=lambda *_a, **_kw: (_ for _ in ()).throw(error))
        with self.assertRaises(RuntimeError) as caught:
            _request_with_retry(session, response.url, attempts=1)
        self.assertIs(error, caught.exception.__cause__)
        self.assertEqual(
            {"failure_reason": "http_access_denied", "http_status": 403},
            _publisher_probe_failure_details(caught.exception),
        )

    def test_live_publisher_http_categories_are_bounded_and_non_authorizing(self):
        cases = [
            (401, "http_access_denied"),
            (403, "http_access_denied"),
            (404, "http_other_error"),
            (429, "http_rate_limited"),
            (503, "http_publisher_unavailable"),
        ]
        for status, category in cases:
            with self.subTest(status=status):
                response = requests.Response()
                response.status_code = status
                response.url = SOURCES["ej"][1]
                def blocked(_config):
                    opaque = requests.HTTPError(
                        "private diagnostic secret must never be serialized",
                        response=response,
                    )
                    raise RuntimeError("opaque wrapper") from opaque
                with patch("scripts.journal_monitor.read_json",
                           return_value=baseline("ej", "136", "678")):
                    state, result = detect_all(
                        {"EJ": {
                            "id": "ej", "enabled": True, "issn": "0013-0133",
                            "announcement_source": SOURCES["ej"][0],
                            "announcement_url": SOURCES["ej"][1],
                        }},
                        {"journals": {}},
                        crossref_fetcher=lambda _config, _baseline: [],
                        issue_signal_fetcher=blocked,
                    )
                probe = public_status(state, result)["publisher_current_issue_signals"]["EJ"]
                self.assertEqual("verification_blocked", probe["status"])
                self.assertEqual("RuntimeError", probe["failure_class"])
                self.assertEqual(category, probe["failure_reason"])
                self.assertEqual(status, probe["http_status"])
                self.assertNotIn("publisher_issue_id", probe)
                self.assertNotIn("private diagnostic secret", str(probe))
                self.assertEqual("healthy", public_status(state, result)["status"])
                self.assertEqual([], result["failed_journals"])

    def test_timeout_and_parser_failures_are_not_misclassified_as_denied(self):
        for error, expected in [
            (requests.Timeout("private URL"), "timeout"),
            (requests.ConnectionError("private URL"), "connection_error"),
            (ValueError("untrusted redirect"), "publisher_header_validation"),
            (RuntimeError("unknown request error"), "unclassified"),
        ]:
            with self.subTest(error=type(error).__name__):
                public = _publisher_probe_failure_details(error)
                self.assertEqual({"failure_reason": expected}, public)
                self.assertNotIn("private URL", str(public))

    def test_empty_official_header_remains_blocked_without_fabrication(self):
        config = {"EJ": {
            "id": "ej", "enabled": True, "issn": "0013-0133",
            "announcement_source": SOURCES["ej"][0],
            "announcement_url": SOURCES["ej"][1],
        }}
        with patch("scripts.journal_monitor.read_json",
                   return_value=baseline("ej", "136", "678")):
            state, result = detect_all(
                config, {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [],
                issue_signal_fetcher=lambda _config: None,
            )
        probe = state["journals"]["EJ"]["publisher_current_issue_signal"]
        self.assertEqual("verification_blocked", probe["status"])
        self.assertEqual("header_unavailable", probe["failure_class"])
        self.assertEqual([], result["confirmed_journals"])
        self.assertNotIn("publisher_issue_id", probe)

    def test_matching_or_older_issue_does_not_trigger_update(self):
        sig = self.signal("ej")
        with patch("scripts.journal_monitor.read_json",
                   return_value=baseline("ej", "136", "679")):
            state, result = detect_all(
                {"EJ": {"id": "ej", "enabled": True, "issn": "0013-0133",
                        "announcement_source": "oup_current_issue"}},
                {"journals": {}},
                crossref_fetcher=lambda _config, _baseline: [],
                issue_signal_fetcher=lambda _config: sig,
            )
        self.assertEqual([], result["confirmed_journals"])
        self.assertEqual("unchanged", state["journals"]["EJ"]["status"])

    def test_future_issuedate_and_wrong_host_fail_closed(self):
        sig = self.signal("ej")
        future = copy.deepcopy(sig)
        future["issue"] = "680"
        future["publication_date"] = "January 2029"
        self.assertIsNone(_first_party_issue_candidate(
            baseline("ej", "136", "678"), None, future
        ))
        untrusted = copy.deepcopy(sig)
        untrusted["source_url"] = "https://evil.example/ej/issue"
        self.assertFalse(_trusted_issue_signal(untrusted))
        self.assertIsNone(_first_party_issue_candidate(
            baseline("ej", "136", "678"), None, untrusted
        ))

    def test_first_party_redirect_to_numbered_issue_remains_verified(self):
        for jid, final in (
            ("ej", "https://academic.oup.com/ej/issue/136/679"),
            ("rfs", "https://academic.oup.com/rfs/issue/39/11"),
            ("jle", "https://www.journals.uchicago.edu/toc/jole/44/4"),
        ):
            kind, source_url, heading, vol, issue, _date = SOURCES[jid]
            with self.subTest(jid=jid), patch(
                "scripts.journal_monitor._request_with_retry",
                return_value=SimpleNamespace(
                    url=final,
                    content=f"<html><h1>{heading}</h1></html>".encode(),
                ),
            ):
                signal = fetch_official_issue_signal({
                    "id": jid, "announcement_source": kind,
                    "announcement_url": source_url,
                })
            self.assertEqual((vol, issue), (signal["volume"], signal["issue"]))

    def test_unapproved_config_and_redirect_are_rejected(self):
        source = SOURCES["ej"][1]
        with self.assertRaisesRegex(ValueError, "unapproved"):
            fetch_official_issue_signal({
                "id": "qje", "announcement_source": "oup_current_issue",
                "announcement_url": source,
            })
        with patch(
            "scripts.journal_monitor._request_with_retry",
            return_value=SimpleNamespace(
                url="https://evil.example/redirect",
                content=b"<h1>Volume 136, Issue 679, October 2026</h1>",
            ),
        ):
            with self.assertRaisesRegex(ValueError, "redirected"):
                fetch_official_issue_signal({
                    "id": "ej", "announcement_source": "oup_current_issue",
                    "announcement_url": source,
                })


if __name__ == "__main__":
    unittest.main()
