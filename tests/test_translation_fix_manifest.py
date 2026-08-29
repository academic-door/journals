from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_translation_fix_manifest import build_manifest
from scripts.translate_issue import _source_hash


class TranslationFixManifestTests(unittest.TestCase):
    def test_counts_valid_missing_and_invalid_without_calling_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issue_dir = root / "public/api/v1/journals/demo/issues"
            cache_dir = root / "cache"
            issue_dir.mkdir(parents=True)
            cache_dir.mkdir()
            articles = [
                {
                    "doi": "10.1000/valid",
                    "title_en": "Valid title",
                    "abstract_en": "The result is 10 percent.",
                    "article_type": "research-article",
                },
                {
                    "doi": "10.1000/missing",
                    "title_en": "Missing title",
                    "abstract_en": "The result is 20 percent.",
                    "article_type": "research-article",
                },
                {
                    "doi": "10.1000/invalid",
                    "title_en": "Invalid title",
                    "abstract_en": "The result is 30 percent.",
                    "article_type": "research-article",
                },
            ]
            issue = {"journal_id": "demo", "articles": articles, "publication_state": "translation_partial"}
            (issue_dir / "demo-1-1.json").write_text(json.dumps(issue), encoding="utf-8")
            valid = {"title_cn": "有效", "abstract_cn": "结果为10%。", "source_hash": _source_hash(articles[0])}
            invalid = {"title_cn": "无效", "abstract_cn": "结果为31%。", "source_hash": _source_hash(articles[2])}
            (cache_dir / "demo.json").write_text(
                json.dumps({"10.1000/valid": valid, "10.1000/invalid": invalid}),
                encoding="utf-8",
            )
            with patch("scripts.build_translation_fix_manifest.validate_translation") as validate:
                def check(article, entry):
                    if entry["title_cn"] == "无效":
                        raise ValueError("numeric mismatch")
                validate.side_effect = check
                result = build_manifest(root, cache_dir, ["demo-1-1"])
            item = result["issues"][0]
            self.assertEqual(3, item["article_count"])
            self.assertEqual(1, item["valid_cached_translations"])
            self.assertEqual(1, len(item["missing_translations"]))
            self.assertEqual(1, len(item["invalid_translations"]))
            self.assertEqual(2, item["expected_model_calls"])


if __name__ == "__main__":
    unittest.main()
