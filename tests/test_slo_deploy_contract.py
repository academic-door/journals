from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github/workflows/deploy.yml"


class SloDeployContractTests(unittest.TestCase):
    def test_deploy_builds_measurement_only_slo_endpoint_after_completeness(self) -> None:
        text = DEPLOY.read_text(encoding="utf-8")
        completeness = text.index("Rebuild release-derived completeness ledger")
        slo = text.index("Build release-derived R4 SLO baseline")
        audit = text.index("Audit overlaid release data")

        self.assertLess(completeness, slo)
        self.assertLess(slo, audit)
        self.assertIn("python scripts/build_slo_metrics.py", text)
        self.assertIn(
            "--completeness public/api/v1/completeness/2026.json",
            text,
        )
        self.assertIn("--monitoring public/api/v1/monitoring.json", text)
        self.assertIn("--backfill-status public/api/v1/backfill-status.json", text)
        self.assertIn("--out-json public/api/v1/slo.json", text)


    def test_public_health_validates_live_source_diagnostics_and_r4_baseline(self) -> None:
        text = (ROOT / ".github/workflows/health.yml").read_text(encoding="utf-8")
        # The health job runs without checkout: validate the actual public
        # endpoints and keep the existing issue-on-failure behavior.
        self.assertIn("/api/v1/source-diagnostics.json", text)
        self.assertIn("/api/v1/slo.json", text)
        self.assertIn('diagnostics = load("source-diagnostics.json")', text)
        self.assertIn('slo = load("slo.json")', text)
        self.assertIn('primary is not health["checks"][key]', text)
        self.assertIn('"reconciled_as_of"', text)
        self.assertIn('item.get("publisher_observed_at")', text)
        self.assertIn('"baseline_metrics_only"', text)
        self.assertIn('slo.get("alerting_enabled") is not False', text)
        self.assertIn('timestamp_field(slo, "slo", "generated_at", 6)', text)
        self.assertIn('if hard_failures:', text)
        self.assertIn('if failure()', text)



if __name__ == "__main__":
    unittest.main()
