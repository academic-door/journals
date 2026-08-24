from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.publish_data_delta import (
    PublishConflict,
    _copy_selected,
    _relative_path,
    apply_delta,
)


class PublishDataDeltaTests(unittest.TestCase):
    def write(self, root: Path, relative: str, value: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def test_applies_changed_journal_and_preserves_fresher_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            generated = root / "generated"
            target = root / "target"
            for tree in (baseline, generated, target):
                self.write(tree, "public/api/v1/journals/a/issues/current.json", "a1")
                self.write(tree, "public/api/v1/journals/b/issues/current.json", "b1")
            self.write(generated, "public/api/v1/journals/a/issues/current.json", "a2")
            self.write(generated, "public/api/v1/journals/a/issues/a-2.json", "archive")
            self.write(target, "public/api/v1/journals/b/issues/b-2.json", "newer")

            report = apply_delta(
                baseline=baseline,
                generated=generated,
                target=target,
                paths=[Path("public/api")],
            )

            self.assertEqual(
                "a2",
                (target / "public/api/v1/journals/a/issues/current.json").read_text(),
            )
            self.assertEqual(
                "newer",
                (target / "public/api/v1/journals/b/issues/b-2.json").read_text(),
            )
            self.assertIn(
                "public/api/v1/journals/a/issues/a-2.json", report["changed"]
            )

    def test_merges_disjoint_json_changes_from_two_publishers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            generated = root / "generated"
            target = root / "target"
            relative = "data/translation-cache/qje.json"
            self.write(baseline, relative, '{"a": "old", "b": "old"}')
            self.write(generated, relative, '{"a": "this-run", "b": "old"}')
            self.write(target, relative, '{"a": "old", "b": "other-run"}')

            apply_delta(
                baseline=baseline,
                generated=generated,
                target=target,
                paths=[Path("data/translation-cache")],
            )

            self.assertEqual(
                '{\n  "a": "this-run",\n  "b": "other-run"\n}\n',
                (target / relative).read_text(),
            )

    def test_rejects_same_path_concurrent_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            generated = root / "generated"
            target = root / "target"
            relative = "public/api/v1/index.json"
            self.write(baseline, relative, "old")
            self.write(generated, relative, "this-run")
            self.write(target, relative, "other-writer")

            with self.assertRaises(PublishConflict):
                apply_delta(
                    baseline=baseline,
                    generated=generated,
                    target=target,
                    paths=[Path("public/api")],
                )

            self.assertEqual("other-writer", (target / relative).read_text())

    def test_merges_same_translation_cache_file_by_latest_article_translation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            generated = root / "generated"
            target = root / "target"
            relative = "data/translation-cache/jf.json"
            self.write(
                baseline,
                relative,
                json.dumps(
                    {
                        "doi-a": {
                            "abstract_cn": "old",
                            "translation": {"translated_at": "2026-08-24T01:00:00+00:00"},
                        },
                        "doi-b": {"abstract_cn": "old-b"},
                    }
                ),
            )
            self.write(
                generated,
                relative,
                json.dumps(
                    {
                        "doi-a": {
                            "abstract_cn": "backfill",
                            "translation": {"translated_at": "2026-08-24T02:00:00+00:00"},
                        },
                        "doi-b": {"abstract_cn": "old-b"},
                        "doi-c": {"abstract_cn": "new-from-backfill"},
                    }
                ),
            )
            self.write(
                target,
                relative,
                json.dumps(
                    {
                        "doi-a": {
                            "abstract_cn": "monitor",
                            "translation": {"translated_at": "2026-08-24T03:00:00+00:00"},
                        },
                        "doi-b": {"abstract_cn": "old-b"},
                        "doi-d": {"abstract_cn": "new-from-monitor"},
                    }
                ),
            )

            apply_delta(
                baseline=baseline,
                generated=generated,
                target=target,
                paths=[Path("data/translation-cache")],
            )

            merged = json.loads((target / relative).read_text(encoding="utf-8"))
            self.assertEqual("monitor", merged["doi-a"]["abstract_cn"])
            self.assertEqual("new-from-backfill", merged["doi-c"]["abstract_cn"])
            self.assertEqual("new-from-monitor", merged["doi-d"]["abstract_cn"])

    def test_deletion_does_not_remove_new_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            generated = root / "generated"
            target = root / "target"
            removed = "data/translation-cache/a.json"
            sibling = "data/translation-cache/b.json"
            self.write(baseline, removed, "old")
            self.write(target, removed, "old")
            self.write(target, sibling, "new")

            report = apply_delta(
                baseline=baseline,
                generated=generated,
                target=target,
                paths=[Path("data/translation-cache")],
            )

            self.assertFalse((target / removed).exists())
            self.assertEqual("new", (target / sibling).read_text())
            self.assertEqual([removed], report["deleted"])

    def test_release_allowlist_rejects_static_site_and_parent_paths(self) -> None:
        for value in ("public/search.js", "../public/api", "/public/api"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                _relative_path(value)

    def test_two_stale_publishers_keep_both_journals_and_rebuild_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            publisher_a = root / "publisher-a"
            publisher_b = root / "publisher-b"
            target = root / "target"
            current_a = "public/api/v1/journals/a/issues/current.json"
            current_b = "public/api/v1/journals/b/issues/current.json"
            index_a = "public/api/v1/journals/a/issues/index.json"
            index_b = "public/api/v1/journals/b/issues/index.json"
            global_index = "public/api/v1/index.json"
            for tree in (baseline, publisher_a, publisher_b, target):
                self.write(tree, current_a, "a1")
                self.write(tree, current_b, "b1")
                self.write(tree, index_a, "derived-old-a")
                self.write(tree, index_b, "derived-old-b")
                self.write(tree, global_index, "derived-old-global")
            self.write(publisher_a, current_a, "a2")
            self.write(publisher_a, index_a, "stale-derived-a")
            self.write(publisher_a, global_index, "stale-derived-a")
            self.write(publisher_b, current_b, "b2")
            self.write(publisher_b, index_b, "stale-derived-b")
            self.write(publisher_b, global_index, "stale-derived-b")
            paths = [Path("public/api/v1/journals")]
            excludes = ["public/api/v1/journals/*/issues/index.json"]

            apply_delta(
                baseline=baseline,
                generated=publisher_a,
                target=target,
                paths=paths,
                excludes=excludes,
            )
            self.write(target, index_a, "fresh-after-a")
            self.write(target, global_index, "fresh-after-a")
            apply_delta(
                baseline=baseline,
                generated=publisher_b,
                target=target,
                paths=paths,
                excludes=excludes,
            )
            self.write(target, index_a, "fresh-a-and-b")
            self.write(target, index_b, "fresh-a-and-b")
            self.write(target, global_index, "fresh-a-and-b")

            self.assertEqual("a2", (target / current_a).read_text())
            self.assertEqual("b2", (target / current_b).read_text())
            self.assertEqual("fresh-a-and-b", (target / index_a).read_text())
            self.assertEqual("fresh-a-and-b", (target / index_b).read_text())
            self.assertEqual("fresh-a-and-b", (target / global_index).read_text())

    def test_snapshot_excludes_derived_history_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            snapshot = root / "snapshot"
            current = "public/api/v1/journals/a/issues/current.json"
            derived = "public/api/v1/journals/a/issues/index.json"
            self.write(source, current, "source")
            self.write(source, derived, "derived")

            _copy_selected(
                source,
                snapshot,
                [Path("public/api/v1/journals")],
                excludes=["public/api/v1/journals/*/issues/index.json"],
            )

            self.assertTrue((snapshot / current).is_file())
            self.assertFalse((snapshot / derived).exists())


if __name__ == "__main__":
    unittest.main()
