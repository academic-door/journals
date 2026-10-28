"""B8 metadata-only state is publication-ready without fabricating abstracts."""
import copy
import json
import unittest
from pathlib import Path

from scripts.b8_rights_qualified import project_b8_issue
from scripts.update_journals import (
    is_archivable_snapshot,
    is_publishable_snapshot,
    issue_content_status,
    issue_publication_state,
    issue_translation_semantics_valid,
    normalize_issue_content,
    validate_issue,
)

ROOT = Path(__file__).resolve().parents[1]


def synthetic_jae_source():
    """Test-only issue-shaped identity, NOT a real JAE source observation."""
    source = json.loads(
        (ROOT / "public/api/v1/journals/jegrowth/issues/current.json")
        .read_text(encoding="utf-8")
    )
    source["journal_id"] = "jae"
    source["issue_id"] = "jae-41-6"
    source["volume"] = "41"
    source["issue"] = "6"
    source["journal_name"] = "Journal of Applied Econometrics"
    source["source_url"] = "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6"
    for article, suffix in zip(source["articles"], ("70067", "70061", "70071"), strict=True):
        article["doi"] = "10.1002/jae." + suffix
        article["source_url"] = "https://onlinelibrary.wiley.com/doi/10.1002/jae." + suffix
        article["sources"] = {"issue": source["source_url"]}
    return source


class MetadataReadyContractTests(unittest.TestCase):
    def make_public(self):
        return project_b8_issue(
            synthetic_jae_source(),
            expected_journal_id="jae", expected_issue_id="jae-41-6",
        )

    def test_metadata_mode_is_schema_valid_ready_without_abstact_claim(self):
        issue = self.make_public()
        self.assertTrue(is_publishable_snapshot(issue))
        self.assertEqual("metadata_complete", issue_content_status(issue))
        self.assertEqual("ready", issue_publication_state(issue))
        self.assertTrue(is_archivable_snapshot(issue))
        self.assertTrue(issue_translation_semantics_valid(issue))
        normalized = normalize_issue_content(copy.deepcopy(issue))
        validate_issue(normalized)
        self.assertTrue(is_publishable_snapshot(normalized))
        self.assertEqual("metadata_complete", normalized["content_status"])
        self.assertEqual("ready", normalized["publication_state"])
        self.assertTrue(is_archivable_snapshot(normalized))
        self.assertEqual(0, normalized["quality"]["abstract_en_complete"])
        self.assertEqual(0, normalized["quality"]["translation_complete"])

    def test_reject_unlicensed_abstract_even_when_issue_claims_ready(self):
        issue = self.make_public()
        issue["articles"][0]["abstract_en"] = "UNLICENSED_PRIVATE_BODY"
        self.assertFalse(is_publishable_snapshot(issue))
        self.assertEqual("blocked", issue_content_status(issue))
        self.assertEqual("blocked", issue_publication_state(issue))
        self.assertFalse(issue_translation_semantics_valid(issue))

    def test_reject_false_rights_counts(self):
        issue = self.make_public()
        issue["public_content"]["translations_cn_visible"] = 3
        self.assertFalse(is_publishable_snapshot(issue))
        self.assertFalse(is_archivable_snapshot(issue))
        issue = self.make_public()
        issue["quality"]["roster_match"] = False
        self.assertFalse(is_publishable_snapshot(issue))

    def test_old_50_issue_model_is_not_reclassified(self):
        original = json.loads(
            (ROOT / "public/api/v1/journals/jegrowth/issues/current.json")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("public_mode", original)
        self.assertTrue(is_publishable_snapshot(original))
        self.assertEqual("complete", issue_content_status(original))
        self.assertEqual("ready", issue_publication_state(original))


if __name__ == "__main__":
    unittest.main()
