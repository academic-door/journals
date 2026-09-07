from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.translate_issue import (  # noqa: E402
    TranslationError,
    request_deepseek_translation,
    validate_translation,
)


DEFAULT_MANIFEST = ROOT / "config" / "translation-benchmark-dois.json"
DEFAULT_PRICING = ROOT / "config" / "translation-provider-pricing.json"
DEFAULT_API_ROOT = ROOT / "public" / "api" / "v1"
DEFAULT_OUTPUT = ROOT / "output" / "translation-provider-benchmark.json"
QWEN_MODEL = "qwen-mt-flash"
QWEN_ENDPOINTS = {
    "beijing": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "singapore": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
    "virginia": "https://dashscope-us.aliyuncs.com/compatible-mode/v1/chat/completions",
}
QWEN_DOMAIN = (
    "Academic economics journal article. Preserve quantitative meaning, "
    "mathematical notation, variable names, causal direction, and standard "
    "economics terminology."
)


class BenchmarkError(RuntimeError):
    pass


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    """Normalize OpenAI-compatible token usage without storing prompt text."""

    payload = usage or {}
    prompt_tokens = _int(payload.get("prompt_tokens", payload.get("input_tokens")))
    cached_tokens = _int(
        payload.get(
            "prompt_cache_hit_tokens",
            (payload.get("input_tokens_details") or {}).get("cached_tokens"),
        )
    )
    cache_miss_tokens = _int(payload.get("prompt_cache_miss_tokens"))
    if not cache_miss_tokens and prompt_tokens:
        cache_miss_tokens = max(0, prompt_tokens - cached_tokens)
    output_tokens = _int(payload.get("completion_tokens", payload.get("output_tokens")))
    details = payload.get("completion_tokens_details") or payload.get("output_tokens_details") or {}
    reasoning_tokens = _int(details.get("reasoning_tokens"))
    total_tokens = _int(payload.get("total_tokens")) or prompt_tokens + output_tokens
    return {
        "input_tokens": prompt_tokens,
        "cached_input_tokens": cached_tokens,
        "cache_miss_input_tokens": cache_miss_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def is_deepseek_peak(at: datetime, pricing: dict[str, Any]) -> bool:
    stamp = at.astimezone(timezone.utc)
    minute = stamp.hour * 60 + stamp.minute
    windows = pricing["providers"]["deepseek"]["peak_utc_windows"]
    return any(_minutes(start) <= minute < _minutes(end) for start, end in windows)


def estimate_cost_usd(
    *,
    provider: str,
    model: str,
    region: str | None,
    usage: dict[str, int],
    at: datetime,
    pricing: dict[str, Any],
) -> float | None:
    million = Decimal("1000000")
    if provider == "deepseek" and model == pricing["providers"]["deepseek"]["model"]:
        band = "peak" if is_deepseek_peak(at, pricing) else "off_peak"
        rates = pricing["providers"]["deepseek"]["rates"][band]
        cost = (
            Decimal(usage["cached_input_tokens"]) * Decimal(str(rates["cache_hit_input"]))
            + Decimal(usage["cache_miss_input_tokens"]) * Decimal(str(rates["cache_miss_input"]))
            + Decimal(usage["output_tokens"]) * Decimal(str(rates["output"]))
        ) / million
        return float(cost)
    if provider == "qwen" and model == pricing["providers"]["qwen"]["model"] and region:
        rates = pricing["providers"]["qwen"]["region_rates"].get(region)
        if not rates:
            return None
        cost = (
            Decimal(usage["input_tokens"]) * Decimal(str(rates["input"]))
            + Decimal(usage["output_tokens"]) * Decimal(str(rates["output"]))
        ) / million
        return float(cost)
    return None


class RecordingResponse:
    def __init__(
        self,
        response: requests.Response,
        *,
        owner: "RecordingSession",
        latency_ms: float,
        occurred_at: datetime,
    ) -> None:
        self._response = response
        self._owner = owner
        self._latency_ms = latency_ms
        self._occurred_at = occurred_at
        self._recorded = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def _record(self, usage: dict[str, Any] | None, success: bool) -> None:
        if self._recorded:
            return
        self._recorded = True
        self._owner.record(
            status_code=int(self._response.status_code),
            success=success,
            latency_ms=self._latency_ms,
            occurred_at=self._occurred_at,
            usage=usage,
        )

    def raise_for_status(self) -> None:
        try:
            self._response.raise_for_status()
        except requests.HTTPError:
            self._record(None, False)
            raise

    def json(self) -> Any:
        body = self._response.json()
        usage = body.get("usage") if isinstance(body, dict) else None
        self._record(usage if isinstance(usage, dict) else None, True)
        return body


class RecordingSession:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        pricing: dict[str, Any],
        region: str | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.pricing = pricing
        self.region = region
        self.records: list[dict[str, Any]] = []
        self._session = requests.Session()

    def post(self, *args: Any, **kwargs: Any) -> RecordingResponse:
        started = time.perf_counter()
        response = self._session.post(*args, **kwargs)
        occurred_at = datetime.now(timezone.utc)
        latency_ms = (time.perf_counter() - started) * 1000
        return RecordingResponse(
            response,
            owner=self,
            latency_ms=latency_ms,
            occurred_at=occurred_at,
        )

    def record(
        self,
        *,
        status_code: int,
        success: bool,
        latency_ms: float,
        occurred_at: datetime,
        usage: dict[str, Any] | None,
    ) -> None:
        normalized = normalize_usage(usage)
        estimated = estimate_cost_usd(
            provider=self.provider,
            model=self.model,
            region=self.region,
            usage=normalized,
            at=occurred_at,
            pricing=self.pricing,
        )
        self.records.append(
            {
                "provider": self.provider,
                "model": self.model,
                "region": self.region,
                "status_code": status_code,
                "success": success,
                "latency_ms": round(latency_ms, 1),
                "occurred_at": occurred_at.replace(microsecond=0).isoformat(),
                "pricing_version": self.pricing["pricing_version"],
                "deepseek_price_band": (
                    "peak" if is_deepseek_peak(occurred_at, self.pricing) else "off_peak"
                ) if self.provider == "deepseek" else None,
                **normalized,
                "estimated_cost_usd": (
                    round(estimated, 9) if estimated is not None else None
                ),
            }
        )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_articles(
    api_root: Path,
    manifest: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    wanted = {
        str(item["doi"]).casefold(): item for item in manifest.get("corpus", [])
    }
    found: dict[str, dict[str, Any]] = {}

    def scan(paths: list[Path]) -> None:
        for path in paths:
            try:
                payload = load_json(path)
            except (OSError, ValueError):
                continue
            for article in payload.get("articles", []):
                doi = str(article.get("doi", "")).casefold()
                if doi in wanted and doi not in found:
                    found[doi] = article

    named = [
        path
        for path in sorted((api_root / "journals").glob("*/issues/*.json"))
        if path.name not in {"current.json", "detected.json", "index.json"}
    ]
    scan(named)
    if len(found) < len(wanted):
        scan(sorted((api_root / "journals").glob("*/issues/current.json")))

    missing = [item["doi"] for key, item in wanted.items() if key not in found]
    if missing:
        raise BenchmarkError("benchmark DOI(s) missing from canonical API: " + ", ".join(missing))
    return [(wanted[key], found[key]) for key in wanted]


def benchmark_deepseek(
    article: dict[str, Any],
    *,
    token: str,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    model = pricing["providers"]["deepseek"]["model"]
    session = RecordingSession(provider="deepseek", model=model, pricing=pricing)
    if not token:
        return {
            "status": "unavailable",
            "error": "DEEPSEEK_API_KEY not configured",
            "calls": [],
        }
    try:
        translated = request_deepseek_translation(
            article,
            token=token,
            session=session,
            retries=3,
        )
        validate_translation(article, translated)
        return {
            "status": "pass",
            "numeric_semantic_pass": True,
            "translation": translated,
            "calls": session.records,
        }
    except (TranslationError, requests.RequestException) as error:
        return {
            "status": "fail",
            "numeric_semantic_pass": False,
            "error": str(error)[:1000],
            "calls": session.records,
        }


def _qwen_text(
    text: str,
    *,
    token: str,
    region: str,
    pricing: dict[str, Any],
    session: RecordingSession,
    retries: int = 3,
) -> str:
    payload = {
        "model": QWEN_MODEL,
        "messages": [{"role": "user", "content": text}],
        "translation_options": {
            "source_lang": "English",
            "target_lang": "Chinese",
            "domains": QWEN_DOMAIN,
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Academic-Door-Journals/1.0",
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.post(
                QWEN_ENDPOINTS[region],
                headers=headers,
                json=payload,
                timeout=90,
            )
            response.raise_for_status()
            body = response.json()
            content = str(body["choices"][0]["message"]["content"]).strip()
            if not content:
                raise BenchmarkError("Qwen-MT returned empty content")
            return content
        except (requests.RequestException, KeyError, IndexError, BenchmarkError) as error:
            last_error = error
            status = (
                error.response.status_code
                if isinstance(error, requests.HTTPError) and error.response is not None
                else 0
            )
            if status in {401, 403, 404}:
                raise
            if attempt + 1 < retries and (status == 429 or status >= 500 or not status):
                time.sleep(max(1, 2**attempt))
                continue
            raise
    raise BenchmarkError(f"Qwen-MT failed: {last_error}")


def benchmark_qwen(
    article: dict[str, Any],
    *,
    token: str,
    pricing: dict[str, Any],
    region_hint: str | None,
) -> tuple[dict[str, Any], str | None]:
    if not token:
        return {
            "status": "unavailable",
            "error": "QWEN_API_KEY not configured",
            "calls": [],
        }, region_hint

    configured = os.environ.get("QWEN_REGION", "").strip().casefold()
    candidates: list[str] = []
    for value in (region_hint, configured, "beijing", "singapore", "virginia"):
        if value and value in QWEN_ENDPOINTS and value not in candidates:
            candidates.append(value)

    all_calls: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for region in candidates:
        session = RecordingSession(
            provider="qwen",
            model=QWEN_MODEL,
            pricing=pricing,
            region=region,
        )
        try:
            title_cn = _qwen_text(
                str(article["title_en"]),
                token=token,
                region=region,
                pricing=pricing,
                session=session,
            )
            abstract_cn = _qwen_text(
                str(article["abstract_en"]),
                token=token,
                region=region,
                pricing=pricing,
                session=session,
            )
            translated = {"title_cn": title_cn, "abstract_cn": abstract_cn}
            validate_translation(article, translated)
            all_calls.extend(session.records)
            return {
                "status": "pass",
                "numeric_semantic_pass": True,
                "region": region,
                "translation": translated,
                "calls": all_calls,
            }, region
        except (TranslationError, BenchmarkError, requests.RequestException) as error:
            all_calls.extend(session.records)
            last_error = error
            status = (
                error.response.status_code
                if isinstance(error, requests.HTTPError) and error.response is not None
                else 0
            )
            if status in {401, 403, 404} and not region_hint:
                continue
            return {
                "status": "fail",
                "numeric_semantic_pass": False,
                "region": region,
                "error": str(error)[:1000],
                "calls": all_calls,
            }, region_hint
    return {
        "status": "unavailable",
        "numeric_semantic_pass": False,
        "error": f"QWEN_API_KEY was rejected by configured/shared regions: {last_error}",
        "calls": all_calls,
    }, region_hint


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for provider in ("deepseek", "qwen"):
        provider_results = [item[provider] for item in results]
        calls = [call for item in provider_results for call in item.get("calls", [])]
        costs = [
            call["estimated_cost_usd"]
            for call in calls
            if call.get("estimated_cost_usd") is not None
        ]
        regions = sorted(
            {
                str(item.get("region"))
                for item in provider_results
                if item.get("region")
            }
        )
        summary[provider] = {
            "articles": len(provider_results),
            "passes": sum(item.get("status") == "pass" for item in provider_results),
            "fails": sum(item.get("status") == "fail" for item in provider_results),
            "unavailable": sum(item.get("status") == "unavailable" for item in provider_results),
            "calls": len(calls),
            "successful_calls": sum(bool(call.get("success")) for call in calls),
            "input_tokens": sum(_int(call.get("input_tokens")) for call in calls),
            "cached_input_tokens": sum(_int(call.get("cached_input_tokens")) for call in calls),
            "cache_miss_input_tokens": sum(_int(call.get("cache_miss_input_tokens")) for call in calls),
            "output_tokens": sum(_int(call.get("output_tokens")) for call in calls),
            "reasoning_tokens": sum(_int(call.get("reasoning_tokens")) for call in calls),
            "total_tokens": sum(_int(call.get("total_tokens")) for call in calls),
            "total_latency_ms": round(sum(float(call.get("latency_ms") or 0) for call in calls), 1),
            "mean_call_latency_ms": (
                round(sum(float(call.get("latency_ms") or 0) for call in calls) / len(calls), 1)
                if calls else None
            ),
            "estimated_cost_usd": round(sum(float(value) for value in costs), 8),
            "regions": regions,
        }
    summary["deepseek"]["explicit_non_thinking_expected"] = True
    summary["deepseek"]["unexpected_reasoning_tokens"] = (
        summary["deepseek"]["reasoning_tokens"] > 0
    )
    return summary


def write_step_summary(report: dict[str, Any]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not target:
        return
    summary = report["summary"]
    lines = [
        "### Translation provider shadow benchmark",
        "",
        f"Pricing version: `{report['pricing_version']}`",
        "",
        "| Provider | Pass | Fail | Calls | Input tokens | Output tokens | Reasoning | Cost (USD) | Mean call latency |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider in ("deepseek", "qwen"):
        item = summary[provider]
        lines.append(
            f"| {provider} | {item['passes']} | {item['fails']} | {item['calls']} | "
            f"{item['input_tokens']} | {item['output_tokens']} | {item['reasoning_tokens']} | "
            f"{item['estimated_cost_usd']:.8f} | {item['mean_call_latency_ms'] or '-'} ms |"
        )
    lines.extend(
        [
            "",
            f"Qwen region(s): `{', '.join(summary['qwen']['regions']) or 'unresolved'}`",
            "",
            "Shadow-only: benchmark output never mutates canonical translations or provider routing.",
        ]
    )
    with open(target, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--api-root", type=Path, default=DEFAULT_API_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    pricing = load_json(args.pricing)
    corpus = resolve_articles(args.api_root, manifest)
    if args.limit > 0:
        corpus = corpus[: args.limit]

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    qwen_key = os.environ.get("QWEN_API_KEY", "").strip()
    results: list[dict[str, Any]] = []
    qwen_region: str | None = None

    for spec, article in corpus:
        deepseek = benchmark_deepseek(article, token=deepseek_key, pricing=pricing)
        qwen, qwen_region = benchmark_qwen(
            article,
            token=qwen_key,
            pricing=pricing,
            region_hint=qwen_region,
        )
        results.append(
            {
                "doi": spec["doi"],
                "classes": spec.get("classes", []),
                "deepseek": deepseek,
                "qwen": qwen,
            }
        )
        print(
            json.dumps(
                {
                    "doi": spec["doi"],
                    "deepseek": deepseek.get("status"),
                    "qwen": qwen.get("status"),
                    "qwen_region": qwen.get("region"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    report = {
        "schema_version": "1.0",
        "benchmark_scope": "shadow_only",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pricing_version": pricing["pricing_version"],
        "production_provider_switch": False,
        "canonical_translation_mutation": False,
        "corpus_size": len(results),
        "summary": summarize(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_step_summary(report)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
