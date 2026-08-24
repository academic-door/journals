from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.import_official_roster_evidence import (
    apply_evidence,
    reconcile_state_files,
    validate_evidence,
)


def provisional_issue() -> dict:
    articles = []
    for sequence in (1, 2):
        doi = f"10.1093/rfs/demo{sequence}"
        articles.append(
            {
                "paper_id": f"doi:{doi}",
                "sequence": sequence,
                "doi": doi,
                "article_type": "research-article",
                "title_en": f"Paper {sequence}",
                "title_cn": f"论文{sequence}",
                "authors": ["Author One"],
                "abstract_en": f"Abstract {sequence} with 2026 results.",
                "abstract_cn": f"摘要{sequence}",
                "source_url": f"https://academic.oup.com/rfs/article/demo{sequence}",
                "sources": {"abstract_en": "crossref"},
                "translation": {"status": "complete"},
                "quality_flags": [],
            }
        )
    return {
        "schema_version": "1.0",
        "issue_id": "rfs-36-2",
        "journal_id": "rfs",
        "journal_name": "The Review of Financial Studies",
        "volume": "36",
        "issue": "2",
        "source_url": "https://academic.oup.com/rfs/issue/36/2",
        "retrieved_at": "2026-08-24T00:00:00+00:00",
        "expected_article_count": 2,
        "research_article_count": 2,
        "status": "incomplete",
        "articles": articles,
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_transport": "crossref",
            "roster_authority": "crossref-provisional",
            "doi_complete": 2,
            "authors_complete": 2,
            "abstract_en_complete": 2,
            "translation_complete": 2,
            "duplicate_count": 0,
            "flags": [
                "publisher_html_blocked_crossref_fallback",
                "crossref_provisional_roster",
            ],
        },
    }


def evidence() -> dict:
    return {
        "schema_version": "1.0",
        "capture_mode": "official-roster-evidence",
        "method": "official-page-read",
        "captured_at": "2026-08-24T00:00:00+00:00",
        "finalized": True,
        "journal_id": "rfs",
        "issue_id": "rfs-36-2",
        "official_url": "https://academic.oup.com/rfs/issue/36/2",
        "excluded_item_count": 3,
        "items": [
            {"sequence": 1, "doi": "10.1093/rfs/demo1", "title_en": "Paper 1"},
            {"sequence": 2, "doi": "10.1093/rfs/demo2", "title_en": "Paper 2"},
        ],
    }


class OfficialRosterEvidenceTests(unittest.TestCase):
    def test_checked_in_official_evidence_is_privacy_safe_and_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        evidence_paths = sorted(
            (root / "data" / "provenance" / "official-rosters").glob("**/*.json")
        )
        self.assertGreaterEqual(len(evidence_paths), 31)
        for path in evidence_paths:
            with self.subTest(path=path.name):
                validate_evidence(json.loads(path.read_text(encoding="utf-8")))

    def test_exact_official_roster_promotes_without_retranslation(self) -> None:
        issue = provisional_issue()
        candidate = apply_evidence(issue, evidence())
        self.assertEqual("official_verified", candidate["source_status"])
        self.assertEqual("ready", candidate["publication_state"])
        self.assertEqual(
            [item["abstract_cn"] for item in issue["articles"]],
            [item["abstract_cn"] for item in candidate["articles"]],
        )
        self.assertNotIn(
            "crossref_provisional_roster", candidate["quality"]["flags"]
        )
        self.assertTrue(candidate["quality"]["official_roster_evidence"]["sequence_sha256"])

    def test_mismatched_order_fails_closed(self) -> None:
        bad = evidence()
        bad["items"] = list(reversed(bad["items"]))
        for sequence, item in enumerate(bad["items"], start=1):
            item["sequence"] = sequence
        with self.assertRaisesRegex(ValueError, "roster/order"):
            apply_evidence(provisional_issue(), bad)

    def test_official_superset_restores_missing_article_truthfully(self) -> None:
        official = evidence()
        official["items"].insert(
            1,
            {
                "sequence": 2,
                "doi": "10.1093/rfs/missing",
                "title_en": "Officially Missing Paper",
                "authors": ["Official Author"],
                "abstract_en": "Official abstract with 2026 evidence.",
                "source_url": "https://academic.oup.com/rfs/article/36/2/1/1",
            },
        )
        official["items"][2]["sequence"] = 3
        candidate = apply_evidence(provisional_issue(), official)
        self.assertEqual(3, candidate["research_article_count"])
        self.assertEqual(
            ["10.1093/rfs/demo1", "10.1093/rfs/missing", "10.1093/rfs/demo2"],
            [article["doi"] for article in candidate["articles"]],
        )
        self.assertEqual("translation_partial", candidate["publication_state"])
        self.assertEqual("official_verified", candidate["source_status"])

    def test_official_superset_requires_detail_for_missing_article(self) -> None:
        official = evidence()
        official["items"].append(
            {
                "sequence": 3,
                "doi": "10.1093/rfs/missing",
                "title_en": "Officially Missing Paper",
            }
        )
        with self.assertRaisesRegex(ValueError, "missing restoration detail"):
            apply_evidence(provisional_issue(), official)

    def test_official_display_markup_matches_plain_title_after_doi_match(self) -> None:
        issue = provisional_issue()
        issue["articles"][0]["title_en"] = "The Case of RD and CO2"
        official = evidence()
        official["items"][0]["title_en"] = "The Case of R&D and CO_{2}"
        candidate = apply_evidence(issue, official)
        self.assertEqual("official_verified", candidate["source_status"])
        self.assertEqual("ready", candidate["publication_state"])

    def test_reconciles_all_matching_state_checkpoints(self) -> None:
        candidate = apply_evidence(provisional_issue(), evidence())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = root / "field-2023-2024.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "issues": {
                            "rfs-36-2": {
                                "status": "source_pending",
                                "last_error": "source authority pending",
                                "retry_class": "source",
                                "next_retry_at": "2026-08-25T00:00:00+00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            updated = reconcile_state_files(candidate, root)
            self.assertEqual([checkpoint], updated)
            entry = json.loads(checkpoint.read_text(encoding="utf-8"))["issues"][
                "rfs-36-2"
            ]
            self.assertEqual("ready", entry["status"])
            self.assertEqual("official_verified", entry["source_status"])
            self.assertNotIn("next_retry_at", entry)

    def test_rejects_private_fields_and_in_progress_issue(self) -> None:
        bad = copy.deepcopy(evidence())
        bad["finalized"] = False
        bad["cookie_header"] = "private"
        with self.assertRaisesRegex(ValueError, "forbidden private field"):
            validate_evidence(bad)


if __name__ == "__main__":
    unittest.main()
