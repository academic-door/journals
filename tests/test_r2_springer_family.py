from pathlib import Path
import unittest

import yaml

from collectors.history import parse_archive


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "field-history.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "refresh-r2-springer.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


class R2SpringerFamilyContractTests(unittest.TestCase):
    def test_ere_uses_live_springer_archive(self):
        journals = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
        definition = journals["ERE"]
        self.assertEqual("springer", definition["platform"])
        self.assertEqual(
            "https://link.springer.com/journal/10640/volumes-and-issues",
            definition["archive_url"],
        )
        self.assertEqual("link.springer.com", definition["allowed_host"])
        self.assertEqual(
            "https://link.springer.com/journal/10640/volumes-and-issues/{volume}-{issue}",
            definition["issue_url_template"],
        )

    def test_springer_archive_parser_requires_allowed_host(self):
        archive_url = "https://link.springer.com/journal/10640/volumes-and-issues"
        html = (
            b'<ul><li><a href="/journal/10640/volumes-and-issues/89-9">'
            b'Issue 9</a> September 2026</li></ul>'
        )
        issues = parse_archive(
            html,
            archive_url,
            journal="ERE",
            platform="springer",
            years=[2026],
            allowed_host="link.springer.com",
        )
        self.assertEqual(["ere-89-9"], [item.issue_id for item in issues])

        wrong_host_html = (
            b'<ul><li><a href="https://example.com/journal/10640/volumes-and-issues/89-9">'
            b'Issue 9</a> September 2026</li></ul>'
        )
        issues = parse_archive(
            wrong_host_html,
            archive_url,
            journal="ERE",
            platform="springer",
            years=[2026],
            allowed_host="link.springer.com",
        )
        self.assertEqual([], issues)

    def test_scheduled_refresh_is_bounded_to_ere_discovery(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("journal: ERE", text)
        self.assertIn('from_year: "2025"', text)
        self.assertIn('to_year: "2026"', text)
        self.assertIn('max_translations: "0"', text)
        self.assertIn("refresh_discovery_only: true", text)
        self.assertIn("backfill-field-history.yml@main", text)

    def test_successful_springer_refresh_triggers_production_deploy(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- Refresh R2 Springer expected sets", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)


if __name__ == "__main__":
    unittest.main()
