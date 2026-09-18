from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.capture_wiley_roster_evidence import _capture, _eligible_record


class WileyMissingArchiveEvidenceTests(unittest.TestCase):
    def test_authoritative_recoverable_wiley_issue_is_eligible_without_archive(self) -> None:
        self.assertTrue(
            _eligible_record(
                {
                    "category": "recoverable",
                    "authority": "official_archive_snapshot",
                },
                {"publisher": "Wiley"},
            )
        )

    def test_candidate_recoverable_issue_is_not_eligible(self) -> None:
        self.assertFalse(
            _eligible_record(
                {
                    "category": "recoverable",
                    "authority": "crossref_candidate",
                },
                {"publisher": "Wiley"},
            )
        )

    def test_capture_validates_official_roster_when_archive_is_missing(self) -> None:
        record = {
            "journal": "TE",
            "issue_id": "te-21-2",
            "year": 2026,
            "volume": "21",
            "issue": "2",
            "official_url": "https://onlinelibrary.wiley.com/toc/15557561/2026/21/2",
        }
        config = {"issn": "1555-7561"}
        inventory = [
            SimpleNamespace(
                doi="10.3982/te1234",
                title="A Research Article",
                source_url="https://onlinelibrary.wiley.com/doi/10.3982/te1234",
                is_research_article=True,
                exclusion_reason="",
            )
        ]
        response = SimpleNamespace(content=b"official-page")
        with (
            patch("scripts.capture_wiley_roster_evidence._session", return_value=object()),
            patch("scripts.capture_wiley_roster_evidence._get", return_value=response),
            patch(
                "scripts.capture_wiley_roster_evidence._parse_issue_inventory",
                return_value=("21", "2", "May 2026", inventory),
            ),
        ):
            evidence = _capture(record, config, None)

        self.assertEqual("te-21-2", evidence["issue_id"])
        self.assertEqual(
            "https://onlinelibrary.wiley.com/toc/15557561/2026/21/2",
            evidence["official_url"],
        )
        self.assertEqual("10.3982/te1234", evidence["items"][0]["doi"])
        self.assertNotIn("source_url", evidence["items"][0])

    def test_existing_archive_still_runs_roster_match_gate(self) -> None:
        record = {
            "journal": "TE",
            "issue_id": "te-21-2",
            "year": 2026,
            "volume": "21",
            "issue": "2",
            "official_url": "https://onlinelibrary.wiley.com/toc/15557561/2026/21/2",
        }
        config = {"issn": "1555-7561"}
        inventory = [
            SimpleNamespace(
                doi="10.3982/te1234",
                title="A Research Article",
                source_url="https://onlinelibrary.wiley.com/doi/10.3982/te1234",
                is_research_article=True,
                exclusion_reason="",
            )
        ]
        response = SimpleNamespace(content=b"official-page")
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "te-21-2.json"
            archive.write_text(
                '{"journal_id":"te","issue_id":"te-21-2","articles":[],"quality":{}}',
                encoding="utf-8",
            )
            with (
                patch("scripts.capture_wiley_roster_evidence._session", return_value=object()),
                patch("scripts.capture_wiley_roster_evidence._get", return_value=response),
                patch(
                    "scripts.capture_wiley_roster_evidence._parse_issue_inventory",
                    return_value=("21", "2", "May 2026", inventory),
                ),
                patch(
                    "scripts.capture_wiley_roster_evidence.apply_evidence",
                    return_value={},
                ) as apply_evidence,
            ):
                _capture(record, config, archive)

        apply_evidence.assert_called_once()


if __name__ == "__main__":
    unittest.main()
