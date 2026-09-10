from pathlib import Path
import unittest

import yaml

from collectors.history import parse_archive


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "field-history.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "refresh-r2-uwpress.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


class R2UWPressFamilyContractTests(unittest.TestCase):
    def test_landecon_uses_live_uwpress_year_archives(self):
        journals = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
        definition = journals["LANDECON"]
        self.assertEqual("highwire", definition["platform"])
        self.assertEqual(
            "https://le.uwpress.org/content/by/year/{year}",
            definition["archive_url_template"],
        )
        self.assertEqual("le.uwpress.org", definition["allowed_host"])
        self.assertEqual(
            "https://le.uwpress.org/content/{volume}/{issue}",
            definition["issue_url_template"],
        )

    def test_highwire_archive_parser_requires_archive_container_and_allowed_host(self):
        archive_url = "https://le.uwpress.org/content/by/year/2026"
        html = b"""<html><body>
          <div class="archive-issue-list">
            <a href="/content/102/1">January 01, 2026: Vol. 102, Issue 1</a>
            <a href="/content/102/2">March 01, 2026: Vol. 102, Issue 2</a>
            <a href="https://example.com/content/102/4">Wrong host</a>
          </div>
          <a href="/content/999/9">Current issue outside archive list</a>
        </body></html>"""
        issues = parse_archive(
            html,
            archive_url,
            journal="LANDECON",
            platform="highwire",
            years=[2026],
            allowed_host="le.uwpress.org",
        )
        self.assertEqual(
            ["landecon-102-1", "landecon-102-2"],
            [item.issue_id for item in issues],
        )

    def test_scheduled_refresh_is_bounded_to_landecon_discovery(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("journal: LANDECON", text)
        self.assertIn('from_year: "2025"', text)
        self.assertIn('to_year: "2026"', text)
        self.assertIn('max_translations: "0"', text)
        self.assertIn("refresh_discovery_only: true", text)
        self.assertIn("backfill-field-history.yml@main", text)

    def test_successful_uwpress_refresh_triggers_production_deploy(self):
        text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- Refresh R2 UW Press expected sets", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)


if __name__ == "__main__":
    unittest.main()
