from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy.yml"


class DeployCompletenessStateOverlayTests(unittest.TestCase):
    def test_deploy_loads_all_backfill_state_shards_from_data_branch(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "git ls-tree -r --name-only origin/data -- data/backfill-state",
            text,
        )
        self.assertIn(
            "grep -E '^data/backfill-state/[^/]+\\\\.json$'",
            text,
        )
        self.assertIn('for state in "${states[@]}"; do', text)
        self.assertNotIn(
            "data/backfill-state/field-2023-2024.json \\\n"
            "              data/backfill-state/field-2025-2026.json",
            text,
        )

    def test_release_ledger_consumes_every_loaded_state(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'for state in "$RUNNER_TEMP/data-state"/*.json; do',
            text,
        )
        self.assertIn('state_args+=(--state "$state")', text)


if __name__ == "__main__":
    unittest.main()
