from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.import_official_roster_evidence import (
    apply_evidence,
    enrich_missing_elsevier,
    main,
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
        "excluded_items": [
            {
                "doi": f"10.1093/rfs/excluded{index}",
                "title_en": f"Excluded {index}",
                "reason": "non-research-title",
            }
            for index in range(1, 4)
        ],
        "items": [
            {"sequence": 1, "doi": "10.1093/rfs/demo1", "title_en": "Paper 1"},
            {"sequence": 2, "doi": "10.1093/rfs/demo2", "title_en": "Paper 2"},
        ],
    }


class OfficialRosterEvidenceTests(unittest.TestCase):
    def test_jpe_official_no_abstract_exception_is_valid_evidence(self) -> None:
        official = {
            "schema_version": "1.0",
            "capture_mode": "official-roster-evidence",
            "method": "browser-authorized",
            "captured_at": "2026-08-27T17:01:11+00:00",
            "finalized": True,
            "journal_id": "jpe",
            "issue_id": "jpe-131-10",
            "official_url": "https://journals.uchicago.edu/toc/jpe/2023/131/10",
            "excluded_item_count": 0,
            "items": [
                {
                    "sequence": 1,
                    "doi": "10.1086/725793",
                    "title_en": "Nobel Lecture: Financial Intermediaries and Financial Crises",
                    "authors": ["Douglas W. Diamond"],
                    "source_url": "https://journals.uchicago.edu/doi/full/10.1086/725793",
                },
                {
                    "sequence": 2,
                    "doi": "10.1086/725792",
                    "title_en": "Nobel Lecture: Multiple Equilibria",
                    "authors": ["Philip H. Dybvig"],
                    "source_url": "https://journals.uchicago.edu/doi/full/10.1086/725792",
                },
            ],
        }
        validate_evidence(official)

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

    def test_enrich_missing_elsevier_skips_non_pii_rosters(self) -> None:
        payload = evidence()
        for item in payload["items"]:
            item.pop("source_id", None)
            item.pop("official_authors", None)
            item.pop("official_article_url", None)
        enriched = enrich_missing_elsevier(payload, provisional_issue())
        self.assertEqual(payload["items"], enriched["items"])

    def test_apply_evidence_excludes_front_matter_row_not_in_archive(self) -> None:
        issue = provisional_issue()
        payload = evidence()
        payload["excluded_item_count"] = 0
        payload["excluded_items"] = []
        payload["items"].append(
            {
                "sequence": 3,
                "doi": "10.1086/729056",
                "title_en": "Index to Volume 131",
            }
        )
        candidate = apply_evidence(issue, payload)
        self.assertEqual(2, len(candidate["articles"]))
        excluded = candidate["quality"]["excluded_items"]
        self.assertIn(
            "10.1086/729056",
            [str(entry.get("doi", "")).strip().lower() for entry in excluded],
        )

    def test_mismatched_order_fails_closed(self) -> None:
        bad = evidence()
        bad["items"] = list(reversed(bad["items"]))
        for sequence, item in enumerate(bad["items"], start=1):
            item["sequence"] = sequence
        with self.assertRaisesRegex(ValueError, "roster/order"):
            apply_evidence(provisional_issue(), bad)

    def test_explicit_official_reorder_reorders_an_exact_archive_set(self) -> None:
        official = evidence()
        official["items"] = list(reversed(official["items"]))
        for sequence, item in enumerate(official["items"], start=1):
            item["sequence"] = sequence
        official["allow_archive_reorder"] = True
        candidate = apply_evidence(provisional_issue(), official)
        self.assertEqual(
            ["10.1093/rfs/demo2", "10.1093/rfs/demo1"],
            [article["doi"] for article in candidate["articles"]],
        )
        self.assertTrue(
            candidate["quality"]["official_roster_evidence"]["archive_reordered"]
        )

    def test_reorder_flag_must_be_boolean(self) -> None:
        official = evidence()
        official["allow_archive_reorder"] = "yes"
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            validate_evidence(official)

    def test_excluded_item_can_use_official_pii_without_a_doi(self) -> None:
        official = evidence()
        official["excluded_items"] = [
            {
                "source_id": "pii:S0000000000000001",
                "title_en": "Editorial Board",
                "reason": "official-editorial",
            }
        ]
        official["excluded_item_count"] = 1
        validate_evidence(official)

    @patch("collectors.metadata_fallback._elsevier_lookup")
    def test_missing_elsevier_metadata_is_enriched_without_changing_roster(
        self, lookup
    ) -> None:
        lookup.return_value = {
            "abstract": "Official Elsevier abstract with 2026 evidence.",
            "status": "success_full_abstract",
        }
        official = evidence()
        official["items"].append(
            {
                "sequence": 3,
                "doi": "10.1016/j.demo.2026.1",
                "title_en": "New Official Paper",
                "source_id": "pii:S0000000000000001",
                "official_authors": ["Official Author"],
                "official_article_url": (
                    "https://www.sciencedirect.com/science/article/pii/"
                    "S0000000000000001"
                ),
            }
        )
        enriched = enrich_missing_elsevier(official, provisional_issue())
        restored = enriched["items"][2]
        self.assertEqual(["Official Author"], restored["authors"])
        self.assertIn("2026", restored["abstract_en"])
        candidate = apply_evidence(provisional_issue(), enriched)
        self.assertEqual(3, candidate["research_article_count"])

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

    def test_official_roster_removes_stale_exclusion_for_restored_article(self) -> None:
        issue = provisional_issue()
        issue["quality"]["excluded_items"] = [
            {
                "doi": "10.1093/rfs/demo1",
                "title_en": "The Case of RD",
                "article_type": "editorial",
                "reason": "editorial_material",
            }
        ]
        candidate = apply_evidence(issue, evidence())
        self.assertNotIn(
            "10.1093/rfs/demo1",
            [item.get("doi") for item in candidate["quality"]["excluded_items"]],
        )

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

    def test_official_exclusion_removes_archived_comment(self) -> None:
        issue = provisional_issue()
        issue["articles"].append(
            {
                **copy.deepcopy(issue["articles"][1]),
                "paper_id": "doi:10.1093/rfs/comment",
                "sequence": 3,
                "doi": "10.1093/rfs/comment",
                "title_en": "A Study: Comment",
            }
        )
        issue["expected_article_count"] = 3
        issue["research_article_count"] = 3
        official = evidence()
        official["excluded_item_count"] = 1
        official["excluded_items"] = [
            {
                "doi": "10.1093/rfs/comment",
                "title_en": "A Study: Comment",
                "reason": "non-research-title",
            }
        ]
        candidate = apply_evidence(issue, official)
        self.assertEqual(2, candidate["research_article_count"])
        self.assertNotIn(
            "10.1093/rfs/comment",
            [article["doi"] for article in candidate["articles"]],
        )
        self.assertEqual(2, candidate["quality"]["translation_complete"])
        self.assertEqual("ready", candidate["publication_state"])

    def test_official_display_markup_matches_plain_title_after_doi_match(self) -> None:
        issue = provisional_issue()
        issue["articles"][0]["title_en"] = "The Case of RD and CO2"
        official = evidence()
        official["items"][0]["title_en"] = "The Case of R&D and CO_{2}"
        candidate = apply_evidence(issue, official)
        self.assertEqual("official_verified", candidate["source_status"])
        self.assertEqual("ready", candidate["publication_state"])

    def test_official_display_punctuation_and_optional_article_match(self) -> None:
        issue = provisional_issue()
        issue["articles"][0]["title_en"] = (
            "Rising Concentration, Superstar Firms, and Implications for Workers †"
        )
        official = evidence()
        official["items"][0]["title_en"] = (
            "Rising Concentration, Superstar Firms, and the Implications for Workers"
        )
        candidate = apply_evidence(issue, official)
        self.assertEqual("official_verified", candidate["source_status"])

    def test_official_display_mathml_matches_compact_metadata_token(self) -> None:
        issue = provisional_issue()
        issue["articles"][0]["title_en"] = "The effect of PM2.5 exposure"
        official = evidence()
        official["items"][0]["title_en"] = "The effect of 𝑃 𝑀 2.5 exposure"
        candidate = apply_evidence(issue, official)
        self.assertEqual("official_verified", candidate["source_status"])

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

    def test_main_defers_evidence_without_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api_root = root / "api"
            evidence_root = root / "evidence"
            api_root.mkdir(parents=True)
            evidence_root.mkdir(parents=True)
            payload = {
                "schema_version": "1.0",
                "capture_mode": "official-roster-evidence",
                "method": "official-page-read",
                "captured_at": "2026-08-26T00:00:00+00:00",
                "finalized": True,
                "journal_id": "demo",
                "issue_id": "demo-1-1",
                "official_url": "https://example.com/toc/1/1",
                "excluded_item_count": 0,
                "items": [],
            }
            evidence_path = evidence_root / "demo-1-1.json"
            evidence_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch(
                "sys.argv",
                [
                    "import_official_roster_evidence.py",
                    str(evidence_path),
                    "--api-root",
                    str(api_root),
                ],
            ):
                self.assertEqual(0, main())


if __name__ == "__main__":
    unittest.main()
