from __future__ import annotations

import unittest

from scripts.audit_privacy import PATTERNS


class PrivacyPatternTests(unittest.TestCase):
    def test_sensitive_headers_and_private_keys_are_detected(self) -> None:
        samples = {
            "authorization_header": "Author" + "ization: Bearer abcdefghijklmnop",
            "cookie_header": "Cook" + "ie: session=abcdefghijklmnop",
            "session_cookie_assignment": 'session_' + 'cookie="abcdefghijklmnop"',
            "ssh_private_key": "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
        }
        for label, sample in samples.items():
            with self.subTest(label=label):
                self.assertRegex(sample, PATTERNS[label])

    def test_placeholders_and_workflow_variable_names_are_allowed(self) -> None:
        harmless = [
            "COMPOSER_DEPLOY_KEY",
            "Authorization is configured locally.",
            'session_cookie="${LOCAL_ONLY}"',
            "Cookie",
        ]
        for sample in harmless:
            with self.subTest(sample=sample):
                self.assertFalse(
                    any(pattern.search(sample) for pattern in PATTERNS.values())
                )


if __name__ == "__main__":
    unittest.main()
