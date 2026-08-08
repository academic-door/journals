from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    name: ROOT / ".github" / "workflows" / name
    for name in (
        "deploy.yml",
        "update-journals.yml",
        "monitor-journals.yml",
        "backfill-history.yml",
        "backfill-field-history.yml",
        "test-email-notification.yml",
    )
}
PUBLISHERS = (
    "update-journals.yml",
    "monitor-journals.yml",
    "backfill-history.yml",
    "backfill-field-history.yml",
)


class PublicDataBranchOwnershipTests(unittest.TestCase):
    def workflow(self, name: str) -> str:
        return WORKFLOWS[name].read_text(encoding="utf-8")

    def test_data_overlay_is_allowlisted_in_every_consumer(self) -> None:
        expected = (
            "git archive origin/data public/api public/project-manifest.json | tar -x"
        )
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                text = self.workflow(name)
                self.assertIn(expected, text)
                self.assertIn("origin/data:public/backfill-status.md", text)
                self.assertNotIn("git archive origin/data public | tar -x", text)

    def test_workflows_never_replace_or_stage_the_whole_public_tree(self) -> None:
        forbidden = {
            "delete": re.compile(r"^\s*rm -rf public(?:\s|$)", re.MULTILINE),
            "stage": re.compile(r"^\s*git add public(?:\s|$)", re.MULTILINE),
            "copy": re.compile(r"cp -R (?:public/\.|\"\$generated/public/\.\") public/"),
        }
        for name in WORKFLOWS:
            text = self.workflow(name)
            for operation, pattern in forbidden.items():
                with self.subTest(workflow=name, operation=operation):
                    self.assertIsNone(pattern.search(text))

    def test_publishers_prune_the_legacy_search_script_from_data(self) -> None:
        for name in PUBLISHERS:
            with self.subTest(workflow=name):
                text = self.workflow(name)
                self.assertIn(
                    "git rm -f --ignore-unmatch public/search.js",
                    text,
                )
                self.assertIn("git add -A public/api", text)

    def test_search_script_remains_a_main_owned_asset(self) -> None:
        search = (ROOT / "public" / "search.js").read_text(encoding="utf-8")
        self.assertIn("const initializeFromQuery = async () =>", search)
        self.assertIn("params.get(\"china\") === \"1\"", search)


if __name__ == "__main__":
    unittest.main()
