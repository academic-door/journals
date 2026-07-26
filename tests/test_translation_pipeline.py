from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import requests

from scripts.translate_issue import (
    TranslationError,
    _extract_json,
    _normalize_written_number_translations,
    _numbers,
    _protect_numbers,
    _restore_numbers,
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


class ForbiddenResponse:
    status_code = 403

    def raise_for_status(self) -> None:
        error = requests.HTTPError("forbidden")
        error.response = self
        raise error


class GoogleResponse:
    def __init__(self, value: str) -> None:
        self.value = value

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list:
        return [[[self.value, "source", None, None]]]


class FallbackSession:
    def post(self, url, *args, **kwargs):
        if "models.github.ai" in url:
            return ForbiddenResponse()
        source = kwargs["data"]["q"]
        return GoogleResponse(
            "政策检验\n9876543210123456789\n"
            "本文研究ATGNUMAEND项政策，发现排放下降ATGNUMBEND，同时福利提高。"
            "估计过程完整保留了论文的研究设计、变量定义与结论方向，"
            "并忠实呈现原始摘要中的经验结果。"
        )


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

    def test_protects_and_restores_numbers_without_reformatting(self) -> None:
        source = (
            "In January 2026 we estimate one effect: "
            "1.15, 509, 7, and 9.3 percent."
        )
        protected, replacements = _protect_numbers(source)
        self.assertNotIn("1.15", protected)
        restored = _restore_numbers(protected, replacements)
        self.assertEqual(
            restored,
            "In 一月 2026 we estimate 一 effect: 1.15, 509, 7, and 9.3%.",
        )

    def test_normalizes_months_and_written_percentages_before_validation(self) -> None:
        source = (
            "From December 2025, adoption rose from three to six percent "
            "and later remained below two percent."
        )
        translated = "自2025年12月起，采用率从3%上升至6%，随后保持在2%以下。"
        normalized = _normalize_written_number_translations(source, translated)
        self.assertEqual(
            normalized,
            "自2025年十二月起，采用率从百分之三上升至百分之六，"
            "随后保持在百分之二以下。",
        )
        self.assertEqual(_numbers(source), _numbers(normalized))

    def test_ignores_inverse_unit_exponents_in_numeric_validation(self) -> None:
        self.assertEqual(
            _numbers("Carbon losses were 2.3 kt C year−1 and 4 USD year−1."),
            ["2.3", "4"],
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

    def test_falls_back_when_github_models_is_forbidden(self) -> None:
        issue = {"journal_id": "test", "articles": [ARTICLE]}
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "test.json"
            result = translate_missing(
                issue,
                cache_path,
                token="forbidden-token",
                session=FallbackSession(),
            )
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(result["translated"], 1)
        self.assertEqual(result["fallback_translated"], 1)
        self.assertEqual(
            cache[ARTICLE["doi"]]["translation"]["provider"],
            "google-translate",
        )


if __name__ == "__main__":
    unittest.main()
