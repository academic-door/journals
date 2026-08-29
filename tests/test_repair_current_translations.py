from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.repair_current_translations import repair_current_issue


def complete_issue(abstract_cn: str) -> dict:
    return {
        "issue_id": "qe-current",
        "journal_id": "qe",
        "research_article_count": 1,
        "articles": [
            {
                "doi": "10.3982/qe-test",
                "title_en": "Coverage",
                "title_cn": "覆盖范围",
                "article_type": "research-article",
                "abstract_en": (
                    "The program reached one hundred households and improved "
                    "outcomes across the study period."
                ),
                "abstract_cn": abstract_cn,
                "translation": {"status": "complete"},
            }
        ],
    }


class RepairCurrentTranslationsTests(unittest.TestCase):
    def test_valid_equivalent_translation_does_not_call_provider(self) -> None:
        issue = complete_issue(
            "该项目覆盖了一百户家庭，并改善了相关结果和整体表现，"
            "同时在整个研究期间保持了稳定的效果。"
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch("scripts.repair_current_translations.translate_missing") as translate:
                result = repair_current_issue(
                    issue,
                    cache_path=Path(directory) / "qe.json",
                    token="unused",
                )
        translate.assert_not_called()
        self.assertEqual("unchanged", result["status"])
        self.assertEqual(0, result["translation_calls"])

    def test_invalid_translation_isolated_for_one_repair_call(self) -> None:
        issue = complete_issue(
            "该项目覆盖了九十户家庭，并改善了相关结果和整体表现，"
            "同时在整个研究期间保持了稳定的效果。"
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "qe.json"
            with patch(
                "scripts.repair_current_translations.translate_missing",
                return_value={"translated": 1, "failed": []},
            ) as translate:
                with patch(
                    "scripts.repair_current_translations.apply_translation_cache"
                ) as apply_cache:
                    result = repair_current_issue(
                        issue,
                        cache_path=cache_path,
                        token="unused",
                    )
        translate.assert_called_once()
        apply_cache.assert_called_once()
        self.assertEqual(cache_path, apply_cache.call_args.kwargs["cache_path"])
        self.assertEqual("unresolved", result["status"])
        self.assertEqual(1, result["translation_calls"])


if __name__ == "__main__":
    unittest.main()
