from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.probe_legacy_chicago_source import OFFICIAL_URL, probe_once


class LegacyChicagoProbeTests(unittest.TestCase):
    def test_exact_single_public_jpe_issue_without_retry(self) -> None:
        fetch = Mock(return_value=SimpleNamespace(content=b"<html></html>"))
        parse = Mock(return_value={
            "volume": "131", "issue": "1",
            "items": [
                {"source_sequence": 1, "doi": "10.1086/a", "title_en": "A", "exclusion_reason": ""},
                {"source_sequence": 2, "doi": "10.1086/b", "title_en": "B", "exclusion_reason": "front-matter"},
            ],
            "flags": [],
        })
        report = probe_once(fetch=fetch, parse=parse)
        self.assertEqual(OFFICIAL_URL, fetch.call_args.args[1])
        self.assertEqual(1, fetch.call_args.kwargs["attempts"])
        self.assertEqual(20, fetch.call_args.kwargs["timeout"])
        self.assertEqual(2, report["toc_item_count"])
        self.assertEqual(1, report["exclusion_count"])
        self.assertTrue(report["no_archive_or_state_mutation"])

    def test_identity_and_source_inventory_fail_closed(self) -> None:
        fetch = Mock(return_value=SimpleNamespace(content=b""))
        for bad in (
            {"volume": "132", "issue": "1", "items": []},
            {"volume": "131", "issue": "1", "items": []},
            {"volume": "131", "issue": "1", "items": [
                {"source_sequence": 1, "doi": "10.1086/a", "title_en": "A"},
                {"source_sequence": 2, "doi": "10.1086/a", "title_en": "B"}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probe_once(fetch=fetch, parse=lambda *_: bad)


if __name__ == "__main__":
    unittest.main()
