import json
import tempfile
import unittest
from pathlib import Path

from scripts.merge_history_shards import merge_shards


class MergeHistoryShardsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
