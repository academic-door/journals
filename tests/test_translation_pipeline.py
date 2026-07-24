from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.translate_issue import (
    TranslationError,
    _extract_json,
    translate_missing,
    validate_translation,
)


ARTICLE = {
    "doi": "10.0000/example",
    "title_en": "A Test of Policy",
    "abstract_en": (
        "We study 96 policies and find that emissions fall by 12.5% while "
        "welfare rises. The estimates preserve the complete research design."
    ),
}


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "title_cn": "政策检验",
                                "abstract_cn": (
                                    "本文研究96项政策，发现排放下降12.5%，同时福利提高。"
                                    "估计过程完整保留了论文的研究设计、变量定义与结论方向，"
                                    "并忠实呈现原始摘要中的经验结果。"
                                ),
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }


class FakeSession:
    def post(self, *args, **kwargs) -> FakeResponse:
        return FakeResponse()


class ExplodingSession:
    def post(self, *args, **kwargs) -> FakeResponse:
        raise AssertionError("valid cache entry must not call the model")


class TranslationPipelineTests(unittest.TestCase):
    def test_extracts_fenced_json(self) -> None:
        data = _extract_json('```json\n{"title_cn":"测试","abstract_cn":"摘要"}\n```')
        self.assertEqual(data["title_cn"], "测试")

    def test_rejects_missing_numbers(self) -> None:
        with self.assertRaises(TranslationError):
            validate_translation(
                ARTICLE,
                {
                    "title_cn": "政策检验",
                    "abstract_cn": "本文研究多项政策并发现排放下降，同时福利提高。" * 5,
                },
            )

    def test_rejects_substring_and_duplicate_number_mismatches(self) -> None:
        article = {
            **ARTICLE,
            "title_en": "Evidence from 5 cities",
            "abstract_en": "We compare 5 cities with 15 regions and 5 policy rounds.",
        }
        with self.assertRaises(TranslationError):
            validate_translation(
                article,
                {
                    "title_cn": "来自15个城市的证据",
                    "abstract_cn": (
                        "本文比较15个地区与5轮政策，并完整说明研究设计、"
                        "识别策略、变量定义和主要经验结论。"
                    ),
                },
            )

    def test_rejects_added_numeric_values(self) -> None:
        with self.assertRaises(TranslationError):
            validate_translation(
                ARTICLE,
                {
                    "title_cn": "2026年政策检验",
                    "abstract_cn": (
                        "本文研究96项政策，发现排放下降12.5%，同时福利提高。"
                        "估计过程完整保留了论文的研究设计、变量定义与结论方向，"
                        "并忠实呈现原始摘要中的经验结果。"
                    ),
                },
            )

    def test_writes_translation_cache_with_provenance(self) -> None:
        issue = {"journal_id": "test", "articles": [ARTICLE]}
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "test.json"
            result = translate_missing(
                issue,
                cache_path,
                token="test-token",
                model="test/model",
                session=FakeSession(),
            )
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["translated"], 1)
        self.assertEqual(cache[ARTICLE["doi"]]["title_cn"], "政策检验")
        self.assertEqual(
            cache[ARTICLE["doi"]]["translation"]["provider"],
            "github-models",
        )
        self.assertRegex(cache[ARTICLE["doi"]]["source_hash"], r"^[0-9a-f]{64}$")

    def test_retranslates_invalid_cached_entry(self) -> None:
        issue = {"journal_id": "test", "articles": [ARTICLE]}
        invalid_cache = {
            ARTICLE["doi"]: {
                "title_cn": "政策检验",
                "abstract_cn": "本文省略数字但保留了其余研究背景与经验结论。" * 5,
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "test.json"
            cache_path.write_text(
                json.dumps(invalid_cache, ensure_ascii=False),
                encoding="utf-8",
            )
            result = translate_missing(
                issue,
                cache_path,
                token="test-token",
                model="test/model",
                session=FakeSession(),
            )
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["invalid_cache_entries"], 1)
        self.assertEqual(result["translated"], 1)
        self.assertIn("96", cache[ARTICLE["doi"]]["abstract_cn"])

    def test_upgrades_valid_legacy_cache_without_retranslation(self) -> None:
        valid_translation = _extract_json(
            FakeResponse().json()["choices"][0]["message"]["content"]
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "test.json"
            cache_path.write_text(
                json.dumps(
                    {ARTICLE["doi"]: valid_translation},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = translate_missing(
                {"journal_id": "test", "articles": [ARTICLE]},
                cache_path,
                token="unused",
                session=ExplodingSession(),
            )
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["translated"], 0)
        self.assertEqual(result["invalid_cache_entries"], 0)
        self.assertEqual(result["upgraded_cache_entries"], 1)
        self.assertRegex(cache[ARTICLE["doi"]]["source_hash"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
