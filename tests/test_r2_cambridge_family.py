from pathlib import Path
import unittest

import yaml

from collectors.history import parse_archive


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "field-history.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "refresh-r2-cambridge.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


class R2CambridgeFamilyContractTests(unittest.TestCase):
    def test_jeh_uses_live_cambridge_archive(self):
        journals = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
        definition = journals["JEH"]
        self.assertEqual("cambridge", definition["platform"])
        self.assertEqual(
            "https://www.cambridge.org/core/journals/journal-of-economic-history/all-issues",
            definition["archive_url"],
        )
        self.assertEqual("www.cambridge.org", definition["allowed_host"])

    def test_cambridge_archive_parser_uses_panel_volume_and_requires_allowed_host(self):
        archive_url = "https://www.cambridge.org/core/journals/journal-of-economic-history/all-issues"
        html = b'''<div id="panel86"><ul><li><a href="/core/journals/journal-of-economic-history/issue/OPAQUE">Issue 2 June 2026 pp. 303-655</a></li></ul></div>'''
        issues = parse_archive(
            html,
            archive_url,
            journal="JEH",
            platform="cambridge",
            years=[2026],
            allowed_host="www.cambridge.org",
        )
        self.assertEqual(["jeh-86-2"], [item.issue_id for item in issues])

        wrong_host_html = b'''<div id="panel86"><a href="https://example.com/core/journals/journal-of-economic-history/issue/OPAQUE">Issue 2 June 2026</a></div>'''
        issues = parse_archive(
            wrong_host_html,
            archive_url,
            journal="JEH",
            platform="cambridge",
            years=[2026],
            allowed_host="www.cambridge.org",
        )
        self.assertEqual([], issues)

    def test_scheduled_refresh_is_bounded_to_jeh_discovery(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("journal: JEH", text)
        self.assertIn('from_year: "2025"', text)
        self.assertIn('to_year: "2026"', text)
        self.assertIn('max_translations: "0"', text)
        self.assertIn("refresh_discovery_only: true", text)
        self.assertIn("backfill-field-history.yml@main", text)

    def test_successful_cambridge_refresh_triggers_production_deploy(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- Refresh R2 Cambridge expected sets", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)


if __name__ == "__main__":
    unittest.main()
