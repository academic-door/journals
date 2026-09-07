from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.benchmark_translation_providers import (
    BenchmarkError,
    estimate_cost_usd,
    is_deepseek_peak,
    load_json,
    normalize_usage,
    resolve_articles,
)


ROOT = Path(__file__).resolve().parents[1]
PRICING = load_json(ROOT / "config" / "translation-provider-pricing.json")


class TranslationProviderBenchmarkTests(unittest.TestCase):
    def test_normalize_deepseek_chat_usage(self) -> None:
        usage = normalize_usage(
            {
                "prompt_tokens": 1000,
                "prompt_cache_hit_tokens": 250,
                "prompt_cache_miss_tokens": 750,
                "completion_tokens": 400,
                "completion_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 1400,
            }
        )
        self.assertEqual(usage["input_tokens"], 1000)
        self.assertEqual(usage["cached_input_tokens"], 250)
        self.assertEqual(usage["cache_miss_input_tokens"], 750)
        self.assertEqual(usage["output_tokens"], 400)
        self.assertEqual(usage["reasoning_tokens"], 0)
        self.assertEqual(usage["total_tokens"], 1400)

    def test_normalize_responses_style_usage(self) -> None:
        usage = normalize_usage(
            {
                "input_tokens": 900,
                "input_tokens_details": {"cached_tokens": 300},
                "output_tokens": 200,
                "output_tokens_details": {"reasoning_tokens": 50},
                "total_tokens": 1100,
            }
        )
        self.assertEqual(usage["cached_input_tokens"], 300)
        self.assertEqual(usage["cache_miss_input_tokens"], 600)
        self.assertEqual(usage["reasoning_tokens"], 50)

    def test_deepseek_peak_windows_are_utc_and_half_open(self) -> None:
        self.assertTrue(
            is_deepseek_peak(datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc), PRICING)
        )
        self.assertTrue(
            is_deepseek_peak(datetime(2026, 9, 7, 9, 59, tzinfo=timezone.utc), PRICING)
        )
        self.assertFalse(
            is_deepseek_peak(datetime(2026, 9, 7, 4, 0, tzinfo=timezone.utc), PRICING)
        )
        self.assertFalse(
            is_deepseek_peak(datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc), PRICING)
        )
        # Weekend hours are always off-peak even when the clock falls inside a
        # weekday peak window.
        self.assertFalse(
            is_deepseek_peak(datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc), PRICING)
        )

    def test_deepseek_cost_uses_cache_and_peak_band(self) -> None:
        usage = {
            "input_tokens": 1000,
            "cached_input_tokens": 100,
            "cache_miss_input_tokens": 900,
            "output_tokens": 500,
            "reasoning_tokens": 0,
            "total_tokens": 1500,
        }
        cost = estimate_cost_usd(
            provider="deepseek",
            model="deepseek-v4-flash",
            region=None,
            usage=usage,
            at=datetime(2026, 9, 7, 2, 0, tzinfo=timezone.utc),
            pricing=PRICING,
        )
        self.assertAlmostEqual(cost or 0, 0.0010574, places=10)

    def test_qwen_cost_is_region_specific(self) -> None:
        usage = {
            "input_tokens": 1000,
            "cached_input_tokens": 0,
            "cache_miss_input_tokens": 1000,
            "output_tokens": 500,
            "reasoning_tokens": 0,
            "total_tokens": 1500,
        }
        cost = estimate_cost_usd(
            provider="qwen",
            model="qwen-mt-flash",
            region="beijing",
            usage=usage,
            at=datetime(2026, 9, 7, tzinfo=timezone.utc),
            pricing=PRICING,
        )
        self.assertAlmostEqual(cost or 0, 0.000241, places=10)

    def test_resolve_articles_uses_doi_without_copying_source_into_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api_root = Path(tmp) / "api" / "v1"
            issue_dir = api_root / "journals" / "demo" / "issues"
            issue_dir.mkdir(parents=True)
            (issue_dir / "demo-1-1.json").write_text(
                json.dumps(
                    {
                        "articles": [
                            {
                                "doi": "10.0000/demo",
                                "title_en": "Policy effects",
                                "abstract_en": "We estimate effects for 100 households and report a 2% change." * 3,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "corpus": [
                    {"doi": "10.0000/demo", "classes": ["percent"]}
                ]
            }
            resolved = resolve_articles(api_root, manifest)
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0][0]["doi"], "10.0000/demo")
            self.assertEqual(resolved[0][1]["title_en"], "Policy effects")

    def test_resolve_articles_fails_closed_on_missing_doi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {"corpus": [{"doi": "10.0000/missing"}]}
            with self.assertRaises(BenchmarkError):
                resolve_articles(Path(tmp), manifest)


if __name__ == "__main__":
    unittest.main()
