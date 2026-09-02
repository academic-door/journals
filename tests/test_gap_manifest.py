import unittest

from scripts.build_gap_manifest import classify_gap


class GapManifestTests(unittest.TestCase):
    def test_ready_issue_is_not_queued(self):
        category, reason = classify_gap(
            archive={
                "archive_exists": True,
                "content_status": "complete",
                "source_status": "official_verified",
                "publication_state": "ready",
            },
            entry={},
            authority="official_archive",
        )
        self.assertEqual(("ready", "content and official source gates passed"), (category, reason))

    def test_crossref_missing_archive_requires_official_browser_evidence(self):
        category, _ = classify_gap(
            archive={
                "archive_exists": False,
                "content_status": "blocked",
                "source_status": "source_pending",
                "publication_state": "blocked",
                "reason": "archive_missing",
            },
            entry={},
            authority="crossref_candidate",
        )
        self.assertEqual("browser_required", category)

    def test_complete_content_without_source_is_not_ready(self):
        category, reason = classify_gap(
            archive={
                "archive_exists": True,
                "content_status": "complete",
                "source_status": "source_pending",
                "publication_state": "source_pending",
            },
            entry={},
            authority="official_archive",
        )
        self.assertEqual("source_pending", category)
        self.assertIn("official source", reason)

    def test_partial_translation_has_its_own_queue(self):
        category, _ = classify_gap(
            archive={
                "archive_exists": True,
                "content_status": "translation_partial",
                "source_status": "official_verified",
                "publication_state": "translation_partial",
            },
            entry={"status": "translation_partial"},
            authority="official_archive",
        )
        self.assertEqual("translation_required", category)

    def test_stale_translation_state_does_not_override_complete_archive(self):
        category, _ = classify_gap(
            archive={
                "archive_exists": True,
                "content_status": "complete",
                "source_status": "source_pending",
                "publication_state": "source_pending",
                "issue": {
                    "research_article_count": 8,
                    "articles": [{}] * 8,
                    "quality": {"translation_complete": 8},
                },
            },
            entry={"status": "translation_partial"},
            authority="crossref_candidate",
        )
        self.assertEqual("source_pending", category)

    def test_official_exclusion_is_never_recollected(self):
        category, _ = classify_gap(
            archive={"archive_exists": False},
            entry={},
            authority="official_archive",
            excluded=True,
        )
        self.assertEqual("excluded_with_official_evidence", category)


if __name__ == "__main__":
    unittest.main()
