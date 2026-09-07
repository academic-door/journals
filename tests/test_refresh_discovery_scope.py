import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import scripts.backfill_history as backfill


class RefreshDiscoveryScopeTests(unittest.TestCase):
    def test_refresh_discovery_only_never_reconciles_issue_state(self):
        original_issue = {
            "journal": "AER",
            "year": 2026,
            "volume": "116",
            "issue": "8",
            "official_url": "https://www.aeaweb.org/journals/aer/issue/116/8",
            "status": "ready",
            "last_error": "",
            "retry_class": "manual",
            "content_status": "complete",
            "source_status": "official_verified",
            "publication_state": "ready",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            config_path = root / "history.yml"
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "updated_at": "2026-08-24T00:00:00+00:00",
                        "issues": {"aer-116-8": original_issue},
                        "discovery": {},
                    }
                ),
                encoding="utf-8",
            )
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "journals": {
                            "AER": {
                                "platform": "year_ranges",
                                "allowed_host": "www.aeaweb.org",
                                "issue_url_template": "https://www.aeaweb.org/journals/aer/issue/{volume}/{issue}",
                                "year_ranges": {2026: {"volume": "116", "issues": [8, 9]}},
                            }
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            argv = [
                "backfill_history.py",
                "--config", str(config_path),
                "--state", str(state_path),
                "--journals", "AER",
                "--from-year", "2026",
                "--to-year", "2026",
                "--refresh-discovery-only",
            ]
            with patch.object(sys, "argv", argv), patch.object(
                backfill,
                "migrate_legacy_state",
                side_effect=AssertionError("discovery-only must not reconcile issue state"),
            ):
                self.assertEqual(backfill.main(), 0)

            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["issues"], {"aer-116-8": original_issue})
            self.assertEqual(payload["discovery"]["AER"]["issue_ids"], ["aer-116-8", "aer-116-9"])


if __name__ == "__main__":
    unittest.main()
