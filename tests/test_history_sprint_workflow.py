from __future__ import annotations

import unittest
from pathlib import Path


class HistorySprintWorkflowTests(unittest.TestCase):
    def test_repairs_jpube_dates_before_strict_audit(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "history-sprint.yml").read_text(
            encoding="utf-8"
        )
        repair = workflow.index("python scripts/repair_history_dates.py")
        audit = workflow.index("python scripts/audit_public_data.py --strict-provenance")
        self.assertIn("--journal JPubE", workflow[repair:audit])
        self.assertLess(repair, audit)


if __name__ == "__main__":
    unittest.main()
