import json
import tempfile
import unittest
from pathlib import Path

from scripts.merge_history_shards import merge_shards


class MergeHistoryShardsTests(unittest.TestCase):
    def test_old_equal_rank_state_cannot_overwrite_new_attempt_diagnostics(self) -> None:
        from scripts.merge_history_shards import merge_state

        base = {
            "issues": {
                "aer-1-1": {
                    "journal": "AER",
                    "status": "source_pending",
                    "last_attempt_at": "2026-08-26T10:00:00+00:00",
                    "attempt_count": 3,
                }
            }
        }
        shard = {
            "issues": {
                "aer-1-1": {
                    "journal": "AER",
                    "status": "source_pending",
                    "attempt_count": 1,
                }
            }
        }
        merged = merge_state(base, shard, {"AER"})
        self.assertEqual(3, merged["issues"]["aer-1-1"]["attempt_count"])

    def test_disjoint_shards_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            shards = Path(temporary) / "shards"
            state_path = root / "data/backfill-state/field-2023-2024.json"
            state_path.parent.mkdir(parents=True)
            base = {
                "issues": {
                    "aer-1-1": {"journal": "AER", "status": "blocked"},
                    "eer-1-1": {"journal": "EER", "status": "blocked"},
                },
                "discovery": {},
            }
            state_path.write_text(json.dumps(base), encoding="utf-8")
            for shard_name, journal, issue_id in (
                ("history-sprint-a", "AER", "aer-1-1"),
                ("history-sprint-b", "EER", "eer-1-1"),
            ):
                shard_state = shards / shard_name / "data/backfill-state"
                shard_output = shards / shard_name / "output"
                shard_state.mkdir(parents=True)
                shard_output.mkdir(parents=True)
                changed = json.loads(json.dumps(base))
                changed["issues"][issue_id]["status"] = "ready"
                (shard_state / state_path.name).write_text(
                    json.dumps(changed), encoding="utf-8"
                )
                (shard_output / "shard-metadata.json").write_text(
                    json.dumps({"journals": [journal]}), encoding="utf-8"
                )
            merge_shards(root, shards)
            merged = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("ready", merged["issues"]["aer-1-1"]["status"])
            self.assertEqual("ready", merged["issues"]["eer-1-1"]["status"])

    def test_old_shard_cannot_regress_a_ready_issue_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            shards = Path(temporary) / "shards"
            archive = root / "public/api/v1/journals/ere/issues/ere-89-8.json"
            archive.parent.mkdir(parents=True)
            archive.write_text(
                json.dumps(
                    {
                        "issue_id": "ere-89-8",
                        "content_status": "complete",
                        "source_status": "publisher_verified",
                        "publication_state": "ready",
                    }
                ),
                encoding="utf-8",
            )
            shard_public = shards / "history-sprint-ere" / "public/api/v1/journals/ere/issues"
            shard_output = shards / "history-sprint-ere" / "output"
            shard_public.mkdir(parents=True)
            shard_output.mkdir(parents=True)
            (shard_public / archive.name).write_text(
                json.dumps(
                    {
                        "issue_id": "ere-89-8",
                        "content_status": "complete",
                        "source_status": "source_pending",
                        "publication_state": "source_pending",
                    }
                ),
                encoding="utf-8",
            )
            (shard_output / "shard-metadata.json").write_text(
                json.dumps({"journals": ["ERE"]}), encoding="utf-8"
            )
            merge_shards(root, shards)
            merged = json.loads(archive.read_text(encoding="utf-8"))
            self.assertEqual("ready", merged["publication_state"])
            self.assertEqual("publisher_verified", merged["source_status"])

    def test_issue_scoped_merge_publishes_only_named_issues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            shards = Path(temporary) / "shards"
            public = root / "public/api/v1/journals/ere/issues"
            public.mkdir(parents=True)
            (public / "ere-85-1.json").write_text(
                json.dumps({"issue_id": "ere-85-1", "publication_state": "source_pending"}),
                encoding="utf-8",
            )
            (public / "ere-87-6.json").write_text(
                json.dumps({"issue_id": "ere-87-6", "publication_state": "ready"}),
                encoding="utf-8",
            )
            shard_public = shards / "history-sprint-ere" / "public/api/v1/journals/ere/issues"
            shard_output = shards / "history-sprint-ere" / "output"
            shard_public.mkdir(parents=True)
            shard_output.mkdir(parents=True)
            (shard_public / "ere-85-1.json").write_text(
                json.dumps({"issue_id": "ere-85-1", "publication_state": "ready"}),
                encoding="utf-8",
            )
            (shard_public / "ere-87-6.json").write_text(
                json.dumps({"issue_id": "ere-87-6", "publication_state": "source_pending"}),
                encoding="utf-8",
            )
            (shard_output / "shard-metadata.json").write_text(
                json.dumps({"journals": ["ERE"]}),
                encoding="utf-8",
            )
            merge_shards(root, shards, issue_ids={"ere-85-1"})
            selected = json.loads((public / "ere-85-1.json").read_text(encoding="utf-8"))
            unrelated = json.loads((public / "ere-87-6.json").read_text(encoding="utf-8"))
            self.assertEqual("ready", selected["publication_state"])
            self.assertEqual("ready", unrelated["publication_state"])

    def test_issue_scoped_merge_keeps_only_selected_translation_cache_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            shards = Path(temporary) / "shards"
            public = root / "public/api/v1/journals/ere/issues"
            public.mkdir(parents=True)
            (public / "ere-85-1.json").write_text(
                json.dumps({"issue_id": "ere-85-1", "articles": [{"doi": "10.1000/a"}]}),
                encoding="utf-8",
            )
            cache = root / "data/translation-cache"
            cache.mkdir(parents=True)
            (cache / "ere.json").write_text(json.dumps({"10.1000/old": {"title_cn": "old"}}), encoding="utf-8")
            shard_cache = shards / "history-sprint-ere/data/translation-cache"
            shard_cache.mkdir(parents=True)
            (shard_cache / "ere.json").write_text(
                json.dumps({"10.1000/a": {"title_cn": "selected"}, "10.1000/b": {"title_cn": "unselected"}}),
                encoding="utf-8",
            )
            shard_public = shards / "history-sprint-ere/public/api/v1/journals/ere/issues"
            shard_public.mkdir(parents=True)
            (shard_public / "ere-85-1.json").write_text(
                json.dumps({"issue_id": "ere-85-1", "articles": [{"doi": "10.1000/a"}]}),
                encoding="utf-8",
            )
            metadata = shards / "history-sprint-ere/output"
            metadata.mkdir(parents=True)
            (metadata / "shard-metadata.json").write_text(json.dumps({"journals": ["ERE"]}), encoding="utf-8")
            merge_history = __import__("scripts.merge_history_shards", fromlist=["merge_shards"])
            merge_history.merge_shards(root, shards, issue_ids={"ere-85-1"})
            result = json.loads((cache / "ere.json").read_text(encoding="utf-8"))
            self.assertIn("10.1000/old", result)
            self.assertIn("10.1000/a", result)
            self.assertNotIn("10.1000/b", result)


if __name__ == "__main__":
    unittest.main()
