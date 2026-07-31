from __future__ import annotations

import unittest

from scripts.monitor_alerts import sync_alerts
from tests.test_monitor_alerts import FakeSession


class ReconcileAlertsTest(unittest.TestCase):
    def test_recovered_journal_alert_closes_even_without_recovered_event(self):
        """A journal fixed outside the monitor must not keep a stale open issue.

        FOODPOLICY recovered on 2026-07-31 — it left `failed_journals` and had a
        fresh successful check — yet issue #32 stayed open, because closing only
        ever happened on an explicit `recovered` event.
        """

        session = FakeSession(
            [
                {
                    "number": 32,
                    "title": "[journal-monitor] FOODPOLICY 连续更新失败",
                    "body": "",
                },
                {
                    "number": 25,
                    "title": "[journal-monitor] JME 连续更新失败",
                    "body": "",
                },
            ]
        )
        synced = sync_alerts(
            {"alerts": {"newly_alerting": [], "recovered": []}},
            repository="academic-door/journals",
            token="test-token",
            session=session,
            reconcile_against={"failed_journals": ["JME"]},
        )
        self.assertEqual(["FOODPOLICY"], synced["closed"])
        self.assertEqual([], synced["created"])

    def test_reconcile_never_touches_the_composer_alert(self):
        session = FakeSession(
            [
                {
                    "number": 40,
                    "title": "[journal-monitor] Composer 同步失败",
                    "body": "",
                }
            ]
        )
        synced = sync_alerts(
            {"alerts": {"newly_alerting": [], "recovered": []}},
            repository="academic-door/journals",
            token="test-token",
            session=session,
            reconcile_against={"failed_journals": []},
        )
        self.assertEqual([], synced["closed"])

    def test_without_reconcile_behaviour_is_unchanged(self):
        session = FakeSession(
            [
                {
                    "number": 32,
                    "title": "[journal-monitor] FOODPOLICY 连续更新失败",
                    "body": "",
                }
            ]
        )
        synced = sync_alerts(
            {"alerts": {"newly_alerting": [], "recovered": []}},
            repository="academic-door/journals",
            token="test-token",
            session=session,
        )
        self.assertEqual([], synced["closed"])


if __name__ == "__main__":
    unittest.main()
