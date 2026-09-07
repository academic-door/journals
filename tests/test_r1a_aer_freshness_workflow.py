from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "backfill-field-history.yml"


class R1AAERFreshnessWorkflowTests(unittest.TestCase):
    def test_scheduled_run_refreshes_aer_live_authoritative_evidence(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Refresh live AER authoritative discovery evidence", text)
        self.assertIn("--journals AER", text)
        self.assertIn("--refresh-discovery-only", text)
        self.assertNotIn("--journals AER,JPE,QE", text)

    def test_scheduled_aer_refresh_forces_guarded_publish(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("force_publish_scheduled_aer=true", text)
        self.assertIn("python scripts/publish_data_delta.py apply", text)


if __name__ == "__main__":
    unittest.main()
