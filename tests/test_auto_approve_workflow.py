from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AutoApproveWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/auto-approve.yml").read_text(
            encoding="utf-8"
        )
        self.approval_job, self.merge_job = self.workflow.split(
            "  merge-approved:", maxsplit=1
        )

    def test_reviewer_app_remains_the_approving_identity(self) -> None:
        self.assertIn("actions/create-github-app-token@v2", self.approval_job)
        self.assertIn("GH_TOKEN: ${{ steps.app-token.outputs.token }}", self.approval_job)
        self.assertIn("-f event=APPROVE", self.approval_job)

    def test_repository_token_merges_only_after_app_approval(self) -> None:
        self.assertIn("needs: auto-approve", self.merge_job)
        self.assertIn("contents: write", self.merge_job)
        self.assertIn("pull-requests: write", self.merge_job)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.merge_job)
        self.assertIn("The auto-reviewer approval is missing", self.merge_job)
        self.assertNotIn("actions/create-github-app-token", self.merge_job)


if __name__ == "__main__":
    unittest.main()
