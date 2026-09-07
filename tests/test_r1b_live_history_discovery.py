from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from collectors.history import discover_official_issues
from scripts.backfill_history import discovery_authority, discovery_refreshed_at
from scripts.build_completeness_ledger import _authority_kind

ROOT = Path(__file__).resolve().parents[1]


class R1BObservedHistoryDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load((ROOT / 'config/field-history.yml').read_text(encoding='utf-8'))['journals']

    def test_jpe_observation_includes_live_issue_134_8(self) -> None:
        issues = discover_official_issues('JPE', self.config['JPE'], years=[2025, 2026])
        ids = [item.issue_id for item in issues]
        self.assertIn('jpe-134-8', ids)
        self.assertEqual(20, len(ids))
        self.assertEqual('official_archive_snapshot', discovery_authority(self.config['JPE']))

    def test_qe_observation_stops_at_17_3(self) -> None:
        issues = discover_official_issues('QE', self.config['QE'], years=[2025, 2026])
        ids = [item.issue_id for item in issues]
        self.assertIn('qe-17-3', ids)
        self.assertNotIn('qe-17-4', ids)
        self.assertEqual(7, len(ids))

    def test_observed_at_is_immutable_freshness_clock(self) -> None:
        self.assertEqual('2026-09-07T10:34:06+00:00', discovery_refreshed_at(self.config['JPE']))
        self.assertEqual('2026-09-07T10:34:06+00:00', discovery_refreshed_at(self.config['QE']))

    def test_static_schedule_without_observation_is_candidate(self) -> None:
        definition = {'platform': 'year_ranges', 'year_ranges': {2026: {'volume': '1', 'issues': [1]}}}
        self.assertEqual('configured_schedule_candidate', discovery_authority(definition))
        self.assertEqual('candidate', _authority_kind('configured_schedule_candidate'))

    def test_observed_urls_are_official_hosts(self) -> None:
        for key in ('JPE', 'QE'):
            path = ROOT / self.config[key]['observed_evidence_path']
            payload = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(key, payload['journal'])
            self.assertTrue(payload['issues'])
            host = self.config[key]['allowed_host']
            self.assertTrue(all(host in item['official_url'] for item in payload['issues']))


if __name__ == '__main__':
    unittest.main()
