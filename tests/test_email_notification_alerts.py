from __future__ import annotations

import unittest

from scripts.email_notification_alerts import ALERT_TITLE, sync_email_alert


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


class EmailAlertTests(unittest.TestCase):
    def test_failure_creates_one_alert(self):
        session = FakeSession()
        action = sync_email_alert(
            {"status": "failure"},
            repository="academic-door/journals",
            token="placeholder-token",
            session=session,
        )
        self.assertEqual("created", action)
        self.assertEqual(["GET", "POST"], [call[0] for call in session.calls])

    def test_existing_alert_is_not_duplicated(self):
        session = FakeSession([{"number": 12, "title": ALERT_TITLE}])
        action = sync_email_alert(
            {"status": "failure"},
            repository="academic-door/journals",
            token="placeholder-token",
            session=session,
        )
        self.assertEqual("unchanged", action)
        self.assertEqual(["GET"], [call[0] for call in session.calls])

    def test_success_closes_alert(self):
        session = FakeSession([{"number": 12, "title": ALERT_TITLE}])
        action = sync_email_alert(
            {"status": "sent"},
            repository="academic-door/journals",
            token="placeholder-token",
            session=session,
        )
        self.assertEqual("closed", action)
        self.assertEqual(["GET", "PATCH"], [call[0] for call in session.calls])

    def test_unconfigured_does_not_create_alert(self):
        session = FakeSession()
        action = sync_email_alert(
            {"status": "unconfigured"},
            repository="academic-door/journals",
            token="placeholder-token",
            session=session,
        )
        self.assertEqual("unchanged", action)
        self.assertEqual(["GET"], [call[0] for call in session.calls])


if __name__ == "__main__":
    unittest.main()
