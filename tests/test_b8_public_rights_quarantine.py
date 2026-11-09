"""Rights quarantine regressions: never leak a new issue without article reuse grants."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.quarantine_b8_public_content import contain_one, _safe_projection


ROOT = Path(__file__).resolve().parents[1]
APPROVED = ROOT / "public/api/v1/journals/ijio/issues/ijio-108-c.json"


class B8PublicRightsContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.api = Path(self.temp.name) / "public/api/v1"
        self.folder = self.api / "journals/ijio/issues"
        self.folder.mkdir(parents=True)
        self.approved = json.loads(APPROVED.read_text(encoding="utf-8"))
        self.assertTrue(_safe_projection(self.approved))
        self.approved_blob = json.dumps(self.approved, ensure_ascii=False, indent=2) + "\n"
        (self.folder / "ijio-108-c.json").write_text(self.approved_blob, encoding="utf-8")

    def unsafe_next(self) -> dict:
        unsafe = copy.deepcopy(self.approved)
        unsafe["issue_id"] = "ijio-109-c"
        unsafe["volume"] = "109"
        unsafe.pop("public_mode", None)
        unsafe.pop("public_content", None)
        unsafe["articles"][0].pop("public_rights", None)
        unsafe["articles"][0]["abstract_en"] = "UNLICENSED_TEST_EN"
        unsafe["articles"][0]["abstract_cn"] = "UNLICENSED_TEST_CN"
        return unsafe

    def write_json(self, name: str, payload: dict) -> None:
        (self.folder / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_unauthorized_newer_current_and_archives_are_contained(self) -> None:
        unsafe = self.unsafe_next()
        self.write_json("current.json", unsafe)
        self.write_json("detected.json", unsafe)
        self.write_json("ijio-109-c.json", unsafe)
        changed = contain_one(self.api, "ijio")
        self.assertEqual(len(changed), 3)
        self.assertEqual(
            json.loads((self.folder / "current.json").read_text(encoding="utf-8")),
            self.approved,
        )
        self.assertFalse((self.folder / "detected.json").exists())
        self.assertFalse((self.folder / "ijio-109-c.json").exists())
        self.assertTrue((self.folder / "ijio-108-c.json").exists())
        self.assertEqual(contain_one(self.api, "ijio"), [])

    def test_verifiably_licensed_current_is_not_downgraded(self) -> None:
        self.write_json("current.json", self.approved)
        self.assertEqual(contain_one(self.api, "ijio"), [])

    def test_missing_accepted_archive_fails_closed(self) -> None:
        (self.folder / "ijio-108-c.json").unlink()
        self.write_json("current.json", self.unsafe_next())
        with self.assertRaisesRegex(ValueError, "no accepted immutable"):
            contain_one(self.api, "ijio")
        self.assertTrue((self.folder / "current.json").exists())

    def test_legacy_or_unknown_title_cannot_enter_guarded_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside B8"):
            contain_one(self.api, "jegrowth")


if __name__ == "__main__":
    unittest.main()
