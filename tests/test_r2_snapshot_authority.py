from __future__ import annotations

import unittest

from scripts.build_completeness_ledger import _authority_kind


class R2ObservedSnapshotAuthorityTests(unittest.TestCase):
    def test_official_archive_snapshot_is_authoritative(self) -> None:
        self.assertEqual("authoritative", _authority_kind("official_archive_snapshot"))

    def test_candidate_authorities_remain_fail_closed(self) -> None:
        self.assertEqual("candidate", _authority_kind("crossref_candidate"))
        self.assertEqual("candidate", _authority_kind("configured_schedule_candidate"))
        self.assertEqual("unknown", _authority_kind("mystery_source"))


if __name__ == "__main__":
    unittest.main()
