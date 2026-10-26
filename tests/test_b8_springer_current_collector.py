"""Source-verified Springer collector activation contract (no network)."""
import copy
import json
import unittest
from unittest.mock import patch

from collectors.springer import STAGE, fetch_current_issue, verified_target
from scripts.update_journals import collector_for


class GrowthCollectorSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stage = json.loads(STAGE.read_text(encoding="utf-8"))
        cls.pinned = next(x for x in stage["journals"] if x["journal"] == "JEGROWTH")
        cls.config = {
            "id": "jegrowth",
            "name": "Journal of Economic Growth",
            "collector": "springer",
            "issn": "1381-4338",
            "current_issue_url": cls.pinned["official_url"],
        }

    def test_only_exact_approved_issue_can_be_collected(self):
        self.assertEqual("jegrowth-31-3", verified_target(self.config)["issue_id"])
        with self.assertRaisesRegex(ValueError, "future Springer issue"):
            fetch_current_issue(self.config, expected_volume="32")
        with self.assertRaisesRegex(ValueError, "future Springer issue"):
            fetch_current_issue(self.config, expected_issue="4")

    def test_reject_source_url_drift(self):
        bad = copy.deepcopy(self.config)
        bad["current_issue_url"] = "https://link.springer.com/journal/10887/volumes-and-issues/31-4"
        with self.assertRaisesRegex(ValueError, "not equal"):
            verified_target(bad)

    def test_reject_foreign_publisher_and_journal(self):
        bad = copy.deepcopy(self.config)
        bad["id"] = "itpf"
        with self.assertRaisesRegex(ValueError, "only for"):
            verified_target(bad)
        bad = copy.deepcopy(self.config)
        bad["current_issue_url"] = "https://crossref.org/works"
        with self.assertRaisesRegex(ValueError, "official Springer"):
            verified_target(bad)

    def test_shared_collector_dispatch_is_bound_and_no_network_until_call(self):
        # collector_for binds the implementation during factory construction;
        # patch it before requesting the callable rather than after binding.
        with patch("collectors.springer.fetch_current_issue", return_value={"journal_id": "jegrowth"}) as mock:
            func = collector_for(self.config)
            self.assertTrue(callable(func))
            self.assertEqual("jegrowth", func()["journal_id"])
            mock.assert_called_once_with(self.config, expected_volume="", expected_issue="")


if __name__ == "__main__":
    unittest.main()
