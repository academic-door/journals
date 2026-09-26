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


if __name__ == "__main__":
    unittest.main()
