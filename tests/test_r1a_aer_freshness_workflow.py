from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "backfill-field-history.yml"


class R1AAERFreshnessWorkflowTests(unittest.TestCase):
    def test_scheduled_run_refreshes_only_live_aer_2025_2026(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Refresh live AER authoritative discovery evidence", text)
        self.assertIn("--state data/backfill-state/field-2025-2026.json", text)
        self.assertIn("--journals AER", text)
        self.assertIn("--from-year 2025", text)
        self.assertIn("--to-year 2026", text)
        self.assertIn("--refresh-discovery-only", text)
        self.assertIn('if [ "$GITHUB_EVENT_NAME" != "schedule" ]', text)
        self.assertNotIn("--journals JPE", text)
        self.assertNotIn("--journals QE", text)

    def test_scheduled_aer_refresh_forces_guarded_publish(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("force_publish_scheduled_aer=true", text)
        self.assertIn('[ "$pending" != "[]" ] || [ "$force_publish_scheduled_aer" = "true" ]', text)
        self.assertIn("python scripts/publish_data_delta.py apply", text)
        self.assertIn("--path data/backfill-state", text)


if __name__ == "__main__":
    unittest.main()
