from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.translate_issue_subset import run_subset


class TranslateIssueSubsetTests(unittest.TestCase):
    def test_only_named_issue_is_processed_and_failure_is_issue_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = root / "api"
            staging = root / "staging/demo"
            cache = root / "cache"
            api.mkdir(parents=True)
            staging.mkdir(parents=True)
            cache.mkdir()
            issue = {
                "journal_id": "demo",
                "issue_id": "demo-1-1",
                "publication_state": "translation_partial",
                "source_status": "publisher_verified",
                "content_status": "complete",
                "quality": {"translation_complete": 0},
                "articles": [
                    {
                        "doi": "10.1000/demo",
                        "title_en": "A title",
                        "title_cn": "",
                        "abstract_en": "A result is 10 percent.",
                        "abstract_cn": "",
                        "article_type": "research-article",
                        "translation": {"status": "missing"},
                    }
                ],
            }
            (staging / "demo-1-1.json").write_text(json.dumps(issue), encoding="utf-8")
            report = {"translated": 0, "failed": [{"doi": "10.1000/demo", "title_en": "A title", "error": "quota"}], "model": "deepseek-chat"}
            with patch("scripts.translate_issue_subset.translate_missing", return_value=report):
                result = run_subset(api, root / "staging", cache, ["demo-1-1"])
            self.assertEqual(["demo-1-1"], result["failed_issue_ids"])
            self.assertEqual(0, result["translation_model_calls"])
            self.assertEqual("10.1000/demo", result["results"][0]["failed"][0]["doi"])
            self.assertEqual("deepseek-chat", result["results"][0]["failed"][0]["model"])


if __name__ == "__main__":
    unittest.main()
