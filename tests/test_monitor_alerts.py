from __future__ import annotations

import unittest

from scripts.monitor_alerts import sync_alerts


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, open_issues=None):
        self.headers = {}
        self.calls = []
        self.open_issues = open_issues or []

    def request(self, method, url, timeout, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "GET":
            return FakeResponse(self.open_issues)
        return FakeResponse({})


class AlertTests(unittest.TestCase):
    def test_creates_new_alert_and_closes_recovered_alert(self):
        session = FakeSession(
            [
                {
                    "number": 7,
                    "title": "[journal-monitor] QJE 连续更新失败",
                    "body": "old",
                }
            ]
        )
        synced = sync_alerts(
            {
                "alerts": {
                    "newly_alerting": ["JDE"],
                    "recovered": ["QJE"],
                }
            },
            repository="academic-door/journals",
            token="test-token",
            session=session,
        )
        self.assertEqual(["JDE"], synced["created"])
        self.assertEqual(["QJE"], synced["closed"])
        self.assertEqual(["GET", "POST", "PATCH"], [call[0] for call in session.calls])

    def test_does_not_duplicate_an_open_alert(self):
        session = FakeSession(
            [
                {
                    "number": 8,
                    "title": "[journal-monitor] JDE 连续更新失败",
                    "body": "old",
                }
            ]
        )
        synced = sync_alerts(
            {
                "alerts": {
                    "newly_alerting": ["JDE"],
                    "recovered": [],
                }
            },
            repository="academic-door/journals",
            token="test-token",
            session=session,
        )
        self.assertEqual([], synced["created"])
        self.assertEqual(["GET"], [call[0] for call in session.calls])

    def test_composer_failure_is_visible_and_recovery_closes_it(self):
        failure_session = FakeSession()
        failed = sync_alerts(
            {"alerts": {"newly_alerting": [], "recovered": []}},
            repository="academic-door/journals",
            token="test-token",
            session=failure_session,
            composer_status="failure",
        )
        self.assertEqual(["COMPOSER"], failed["created"])
        self.assertEqual(["GET", "POST"], [call[0] for call in failure_session.calls])

        recovery_session = FakeSession(
            [
                {
                    "number": 9,
                    "title": "[journal-monitor] Composer 同步失败",
                    "body": "old",
                }
            ]
        )
        recovered = sync_alerts(
            {"alerts": {"newly_alerting": [], "recovered": []}},
            repository="academic-door/journals",
            token="test-token",
            session=recovery_session,
            composer_status="success",
        )
        self.assertEqual(["COMPOSER"], recovered["closed"])
        self.assertEqual(["GET", "PATCH"], [call[0] for call in recovery_session.calls])


if __name__ == "__main__":
    unittest.main()
