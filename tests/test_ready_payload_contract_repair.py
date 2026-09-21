from __future__ import annotations

import unittest

from scripts.repair_ready_payload_contract import repaired_ready_payload


class ReadyPayloadContractRepairTests(unittest.TestCase):
    def _ready(self, issue_id: str = "demo-1-1") -> dict:
        return {
            "issue_id": issue_id,
            "publication_state": "ready",
            "content_status": "complete",
            "status": "ready",
            "articles": [],
            "quality": {"flags": []},
        }

    def test_missing_marker_becomes_explicit_false(self) -> None:
        payload = self._ready()
        repaired = repaired_ready_payload(payload)
        self.assertIs(repaired["development_sample"], False)
        self.assertNotIn("development_sample", payload)

    def test_explicit_development_marker_is_never_overwritten(self) -> None:
        payload = self._ready()
        payload["development_sample"] = True
        repaired = repaired_ready_payload(payload)
        self.assertIs(repaired["development_sample"], True)

    def test_same_issue_current_ready_refreshes_stale_archive(self) -> None:
        archive = self._ready("jeem-140-c")
        archive["status"] = "incomplete"
        archive["quality"] = {"flags": ["official_order_unverified"]}
        current = self._ready("jeem-140-c")
        current["source_status"] = "official_verified"
        current["quality"] = {"flags": [], "browser_order_verification": {"pii_sequence_matched": True}}
        repaired = repaired_ready_payload(archive, current=current)
        self.assertEqual("ready", repaired["status"])
        self.assertEqual([], repaired["quality"]["flags"])
        self.assertEqual("official_verified", repaired["source_status"])
        self.assertIs(repaired["development_sample"], False)

    def test_different_current_issue_never_replaces_history(self) -> None:
        archive = self._ready("demo-1-1")
        archive["source_status"] = "publisher_verified"
        current = self._ready("demo-2-1")
        current["source_status"] = "official_verified"
        repaired = repaired_ready_payload(archive, current=current)
        self.assertEqual("demo-1-1", repaired["issue_id"])
        self.assertEqual("publisher_verified", repaired["source_status"])

    def test_nonready_payload_is_unchanged(self) -> None:
        payload = self._ready()
        payload["publication_state"] = "source_pending"
        repaired = repaired_ready_payload(payload)
        self.assertEqual(payload, repaired)
        self.assertNotIn("development_sample", repaired)


if __name__ == "__main__":
    unittest.main()
