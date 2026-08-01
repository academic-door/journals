from __future__ import annotations

import copy
import unittest

from scripts.import_browser_authorized_snapshot import (
    build_candidate,
    build_gap_report,
    validate_snapshot,
)


def snapshot_fixture() -> dict:
    return {
        "schema_version": "1.0",
        "capture_mode": "browser-authorized",
        "journal_id": "jde",
        "journal_name": "Journal of Development Economics",
        "volume": "183",
        "issue": "C",
        "publication_date": "September 2026",
        "source_url": "https://www.sciencedirect.com/journal/journal-of-development-economics/vol/183/suppl/C",
        "captured_at": "2026-08-02T00:00:00+00:00",
        "institutional_access_confirmed": True,
        "items": [
            {
                "official_order": 1,
                "raw_type": "Editorial board",
                "title_en": "Editorial Board",
                "authors": [],
                "abstract_en": "",
                "pii": "S0304387826001690",
                "doi": "10.1016/S0304-3878(26)00169-0",
                "source_url": "https://www.sciencedirect.com/science/article/pii/S0304387826001690",
            },
            {
                "official_order": 2,
                "raw_type": "Research article",
                "title_en": "Right-sizing incentives",
                "authors": ["Edward N. Okeke", "Isa S. Abubakar"],
                "abstract_en": "An official publisher abstract.",
                "pii": "S0304387826001173",
                "doi": "10.1016/j.jdeveco.2026.103834",
                "source_url": "https://www.sciencedirect.com/science/article/pii/S0304387826001173",
            },
            {
                "official_order": 3,
                "raw_type": "Short communication",
                "title_en": "A short communication",
                "authors": ["Author One"],
                "abstract_en": "Another official publisher abstract.",
                "pii": "S0304387826001240",
                "doi": "10.1016/j.jdeveco.2026.103841",
                "source_url": "https://www.sciencedirect.com/science/article/pii/S0304387826001240",
            },
        ],
    }


class BrowserAuthorizedSnapshotTests(unittest.TestCase):
    def test_builds_schema_valid_local_candidate(self) -> None:
        candidate = build_candidate(snapshot_fixture())
        self.assertEqual("jde-183-c", candidate["issue_id"])
        self.assertEqual(3, candidate["content_counts"]["official_items"])
        self.assertEqual(2, candidate["content_counts"]["publishable_items"])
        self.assertEqual(1, candidate["content_counts"]["editorial_material"])
        self.assertEqual([2, 3], [item["source_sequence"] for item in candidate["articles"]])
        self.assertEqual("browser-authorized-local", candidate["quality"]["roster_transport"])
        self.assertIn("translation_incomplete", candidate["quality"]["flags"])

    def test_rejects_private_session_fields(self) -> None:
        snapshot = snapshot_fixture()
        snapshot["cookie_header"] = "private"
        self.assertIn("forbidden private field", "\n".join(validate_snapshot(snapshot)))

    def test_rejects_truncated_authors_and_missing_abstract(self) -> None:
        snapshot = snapshot_fixture()
        snapshot["items"][1]["authors"] = ["First Author", "..."]
        snapshot["items"][1]["abstract_en"] = ""
        errors = "\n".join(validate_snapshot(snapshot))
        self.assertIn("authors are empty or truncated", errors)
        self.assertIn("official English abstract missing", errors)

    def test_gap_report_keeps_publication_blocked_until_translation(self) -> None:
        snapshot = snapshot_fixture()
        candidate = build_candidate(snapshot)
        current = {
            "issue_id": "jde-182-c",
            "volume": "182",
            "publication_date": "June 2026",
            "articles": [],
        }
        report = build_gap_report(snapshot, candidate, current)
        self.assertTrue(report["freshness_gap"]["new_issue_detected"])
        self.assertEqual("passed", report["source_integrity_gate"]["status"])
        self.assertEqual("blocked", report["publication_gate"]["status"])

    def test_rejects_duplicate_doi_and_non_official_url(self) -> None:
        snapshot = snapshot_fixture()
        duplicate = copy.deepcopy(snapshot["items"][1])
        duplicate["official_order"] = 4
        duplicate["pii"] = "S0304387826009999"
        duplicate["source_url"] = "https://example.com/article"
        snapshot["items"].append(duplicate)
        errors = "\n".join(validate_snapshot(snapshot))
        self.assertIn("duplicate DOI", errors)
        self.assertIn("source_url is not official ScienceDirect", errors)


if __name__ == "__main__":
    unittest.main()
