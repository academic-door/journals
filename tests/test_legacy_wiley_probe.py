"""Regression for bounded legacy Wiley source feasibility probe."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.probe_legacy_wiley_source import OFFICIAL_URL, probe_once


class LegacyWileyProbeTests(unittest.TestCase):
    def test_exact_single_issue_page_and_no_retry(self) -> None:
        fetch = Mock(return_value=SimpleNamespace(content=b"<html></html>"))
        rows = [
            SimpleNamespace(is_research_article=True, doi="10.1111/example.1", source_sequence=1),
            SimpleNamespace(is_research_article=False, doi="", source_sequence=2),
        ]
        parse = Mock(return_value=("105", "1", "January 2023", rows))
        result = probe_once(fetch=fetch, parse=parse)
        self.assertEqual(OFFICIAL_URL, fetch.call_args.args[1])
        self.assertEqual(1, fetch.call_args.kwargs["attempts"])
        self.assertEqual((5, 20), fetch.call_args.kwargs["timeout"])
        self.assertEqual("transport_parse_succeeded", result["result"])
        self.assertEqual(1, result["research_count"])
        self.assertEqual(1, result["excluded_count"])
        self.assertTrue(result["no_archive_or_state_mutation"])

    def test_does_not_accept_mismatched_identity(self) -> None:
        fetch = Mock(return_value=SimpleNamespace(content=b""))
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            probe_once(fetch=fetch, parse=lambda *_: ("106", "1", "", []))

    def test_does_not_accept_duplicate_doi_or_ordinal(self) -> None:
        fetch = Mock(return_value=SimpleNamespace(content=b""))
        for rows in (
            [SimpleNamespace(is_research_article=True, doi="", source_sequence=1)],
            [
                SimpleNamespace(is_research_article=True, doi="10.1/foo", source_sequence=1),
                SimpleNamespace(is_research_article=True, doi="10.1/foo", source_sequence=2),
            ],
            [
                SimpleNamespace(is_research_article=True, doi="10.1/foo", source_sequence=1),
                SimpleNamespace(is_research_article=True, doi="10.1/bar", source_sequence=1),
            ],
        ):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                probe_once(fetch=fetch, parse=lambda *_: ("105", "1", "", rows))


if __name__ == "__main__":
    unittest.main()
