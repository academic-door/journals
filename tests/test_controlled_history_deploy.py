from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = ROOT / ".github/workflows/deploy.yml"


class ControlledHistoryDeployTests(unittest.TestCase):
    def test_controlled_history_completion_triggers_pages_deploy(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- Controlled history evidence dispatch", workflow)


if __name__ == "__main__":
    unittest.main()
