from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "backfill-field-history.yml"


class R2AEAFamilyFreshnessWorkflowTests(unittest.TestCase):
    def test_scheduled_run_refreshes_live_aea_family_authoritative_evidence(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Refresh live AEA-family authoritative discovery evidence", text)
        self.assertIn("--journals AER,AEJAPP,AEJMACRO,AEJMICRO,AEJPOL", text)
        self.assertIn("--refresh-discovery-only", text)
        self.assertNotIn("AER,JPE,QE", text)

    def test_scheduled_aea_family_refresh_forces_guarded_publish(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("force_publish_scheduled_aea_family=true", text)
        self.assertIn("python scripts/publish_data_delta.py apply", text)


if __name__ == "__main__":
    unittest.main()
