"""Fail-closed 49->50 B8 activation bridge, with no production writes."""
import copy
import json
import unittest
from pathlib import Path

import yaml

from scripts.b8_activation_overlay import review_activation_transition

ROOT = Path(__file__).resolve().parents[1]


class B8ActivationBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "config/journal-expansion-b8.json").read_text())
        cls.configs = yaml.safe_load((ROOT / "config/journals.yml").read_text())["journals"]

    def test_existing_49_live_is_unmodified(self):
        baseline = copy.deepcopy(self.configs)
        baseline.pop("JEGROWTH", None)
        self.assertEqual(49, len(baseline))
        self.assertEqual(
            "no_op",
            review_activation_transition(self.manifest, baseline, {}, None),
        )

    def test_reject_unapproved_50th_title(self):
        conf = copy.deepcopy(self.configs)
        conf["JEGROWTH"] = {"id": "jegrowth", "enabled": True, "collector": "crossref", "current_issue_url": ""}
        with self.assertRaisesRegex(ValueError, "source-qualified"):
            review_activation_transition(self.manifest, conf, {}, None)

    def test_reject_unready_50th_title(self):
        conf = copy.deepcopy(self.configs)
        conf["JEGROWTH"] = {
            "id": "jegrowth", "enabled": True, "collector": "springer",
            "current_issue_url": "https://link.springer.com/journal/10887/volumes-and-issues/31-3",
        }
        with self.assertRaisesRegex(ValueError, "lacks"):
            review_activation_transition(self.manifest, conf, {}, None)


if __name__ == "__main__":
    unittest.main()
