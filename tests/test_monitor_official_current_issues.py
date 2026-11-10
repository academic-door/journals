"""Offline first-party current-issue issue-existence freshness regressions.

Publisher issue headers may trigger investigation when Crossref misses an
issue, but MUST NOT fabricate official full TOCs, DOIs or abstract rights.
"""
from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.journal_monitor import (
    _first_party_issue_candidate,
    _trusted_issue_signal,
    detect_all,
    fetch_official_issue_signal,
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
