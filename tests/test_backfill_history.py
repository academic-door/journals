from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.history import HistoricalIssue
from scripts.backfill_history import atomic_write_json, collector_for_issue


class BackfillHistoryTests(unittest.TestCase):
    def test_atomic_checkpoint_write_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_write_json(path, {"status": "translation_partial"})
            self.assertEqual(
                {"status": "translation_partial"},
                json.loads(path.read_text(encoding="utf-8")),
            )
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_historical_collector_uses_exact_url_not_current_url(self) -> None:
        config = {
            "id": "aer",
            "name": "American Economic Review",
            "collector": "aea",
            "current_issue_url": "https://www.aeaweb.org/journals/aer/current-issue",
        }
        exact = "https://www.aeaweb.org/issues/828"
        with patch("collectors.aea.fetch_current_issue", return_value={"issue_id": "aer-115-12"}) as fetch:
            result = collector_for_issue(config, exact)()
        self.assertEqual("aer-115-12", result["issue_id"])
        fetch.assert_called_once_with(
            exact,
            journal_id="aer",
            journal_name="American Economic Review",
        )

    def test_issue_identity_is_stable(self) -> None:
        issue = HistoricalIssue("QJE", 2025, "140", "2", "https://academic.oup.com/qje/issue/140/2")
        self.assertEqual("qje-140-2", issue.issue_id)


if __name__ == "__main__":
    unittest.main()
