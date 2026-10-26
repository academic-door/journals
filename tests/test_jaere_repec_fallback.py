from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.update_journals import fallback_collector_for


class JaereFallbackTests(unittest.TestCase):
    def test_exact_volume_issue_uses_publisher_supplied_repec(self):
        root = Path(__file__).resolve().parents[1]
        config = yaml.safe_load(
            (root / "config/journals.yml").read_text()
        )["journals"]["JAERE"]
        expected = {"issue_id": "jaere-13-6"}
        with patch(
            "collectors.metadata_fallback.fetch_repec_history_issue",
            return_value=expected,
        ) as mock:
            value = fallback_collector_for(
                config, expected_volume="13", expected_issue="6"
            )()
        self.assertEqual(expected, value)
        self.assertEqual("13", mock.call_args.kwargs["volume"])
        self.assertEqual("6", mock.call_args.kwargs["issue"])
        self.assertEqual("ucp/jaerec", mock.call_args.kwargs["repec_series_code"])

    def test_jpe_repec_collector_is_not_injected(self):
        root = Path(__file__).resolve().parents[1]
        config = yaml.safe_load(
            (root / "config/journals.yml").read_text()
        )["journals"]["JPE"]
        with patch(
            "collectors.metadata_fallback.fetch_repec_history_issue"
        ) as mock_repec, patch(
            "collectors.metadata_fallback.fetch_crossref_current_issue",
            return_value={"issue_id": "jpe-134-10"},
        ):
            result = fallback_collector_for(
                config, expected_volume="134", expected_issue="10"
            )()
        self.assertEqual("jpe-134-10", result["issue_id"])
        mock_repec.assert_not_called()
