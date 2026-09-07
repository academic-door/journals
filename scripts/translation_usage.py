from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICING_PATH = ROOT / "config" / "translation-provider-pricing.json"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    """Normalize provider token usage without retaining prompt or response text."""

    payload = usage or {}
    input_tokens = _int(payload.get("prompt_tokens", payload.get("input_tokens")))
    cached_input_tokens = _int(
        payload.get(
            "prompt_cache_hit_tokens",
            (payload.get("input_tokens_details") or {}).get("cached_tokens"),
        )
    )
    cache_miss_input_tokens = _int(payload.get("prompt_cache_miss_tokens"))
    if not cache_miss_input_tokens and input_tokens:
        cache_miss_input_tokens = max(0, input_tokens - cached_input_tokens)
    output_tokens = _int(payload.get("completion_tokens", payload.get("output_tokens")))
    details = (
        payload.get("completion_tokens_details")
        or payload.get("output_tokens_details")
        or {}
    )
    reasoning_tokens = _int(details.get("reasoning_tokens"))
    total_tokens = _int(payload.get("total_tokens")) or input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_miss_input_tokens": cache_miss_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def load_pricing(path: Path = DEFAULT_PRICING_PATH) -> dict[str, Any] | None:
    """Load the versioned pricing table. Telemetry must never break translation."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def is_deepseek_peak(at: datetime, pricing: dict[str, Any]) -> bool:
    """Return whether the call falls in the official weekday Beijing peak band."""

    stamp = at.astimezone(timezone.utc)
    # Official peak windows are Monday-Friday Beijing time. The configured UTC
    # windows (01:00-04:00 and 06:00-10:00) never cross a calendar-day boundary
    # relative to Beijing, so the UTC weekday is the same weekday for each band.
    if stamp.weekday() >= 5:
        return False
    minute = stamp.hour * 60 + stamp.minute
    windows = pricing["providers"]["deepseek"]["peak_utc_windows"]
    return any(_minutes(start) <= minute < _minutes(end) for start, end in windows)


def estimate_cost_usd(
    *,
    provider: str,
    model: str,
    usage: dict[str, int],
    at: datetime,
    pricing: dict[str, Any],
    region: str | None = None,
) -> tuple[float | None, str | None]:
    """Estimate one billed call from the explicit versioned pricing table."""

    million = Decimal("1000000")
    if provider == "deepseek":
        config = pricing.get("providers", {}).get("deepseek", {})
        if model != config.get("model"):
            return None, None
        band = "peak" if is_deepseek_peak(at, pricing) else "off_peak"
        rates = config.get("rates", {}).get(band, {})
        try:
            cost = (
                Decimal(usage["cached_input_tokens"])
                * Decimal(str(rates["cache_hit_input"]))
                + Decimal(usage["cache_miss_input_tokens"])
                * Decimal(str(rates["cache_miss_input"]))
                + Decimal(usage["output_tokens"])
                * Decimal(str(rates["output"]))
            ) / million
        except (KeyError, ArithmeticError, ValueError):
            return None, band
        return float(cost), band

    if provider == "qwen":
        config = pricing.get("providers", {}).get("qwen", {})
        if model != config.get("model") or not region:
            return None, None
        rates = config.get("region_rates", {}).get(region)
        if not isinstance(rates, dict):
            return None, None
        try:
            cost = (
                Decimal(usage["input_tokens"]) * Decimal(str(rates["input"]))
                + Decimal(usage["output_tokens"]) * Decimal(str(rates["output"]))
            ) / million
        except (KeyError, ArithmeticError, ValueError):
            return None, region
        return float(cost), region

    return None, None


def build_usage_event(
    *,
    provider: str,
    model: str,
    usage_payload: dict[str, Any] | None,
    latency_ms: float,
    occurred_at: datetime,
    status_code: int,
    success: bool,
    pricing: dict[str, Any] | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Build one non-sensitive provider-call accounting event."""

    normalized = normalize_usage(usage_payload)
    cost: float | None = None
    price_band: str | None = None
    if pricing is not None:
        try:
            cost, price_band = estimate_cost_usd(
                provider=provider,
                model=model,
                usage=normalized,
                at=occurred_at,
                pricing=pricing,
                region=region,
            )
        except Exception:
            # Cost observability must never alter translation behavior.
            cost = None
            price_band = None
    return {
        "provider": provider,
        "model": model,
        "status_code": int(status_code or 0),
        "success": bool(success),
        "latency_ms": round(max(0.0, float(latency_ms)), 1),
        "price_band": price_band,
        "estimated_cost_usd": cost,
        **normalized,
    }


def aggregate_usage(
    events: list[dict[str, Any]],
    *,
    pricing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate call events for reports without persisting per-article content."""

    summary: dict[str, Any] = {
        "pricing_version": (
            str(pricing.get("pricing_version", "")) if isinstance(pricing, dict) else ""
        ),
        "calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "billed_calls": 0,
        "unpriced_billed_calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_miss_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "total_latency_ms": 0.0,
        "estimated_cost_usd": 0.0,
        "providers": {},
    }

    def empty_provider() -> dict[str, Any]:
        return {
            "calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "billed_calls": 0,
            "unpriced_billed_calls": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "cache_miss_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "total_latency_ms": 0.0,
            "estimated_cost_usd": 0.0,
            "models": [],
            "price_bands": [],
        }

    token_fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_miss_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    for event in events:
        provider = str(event.get("provider", "unknown")) or "unknown"
        bucket = summary["providers"].setdefault(provider, empty_provider())
        for target in (summary, bucket):
            target["calls"] += 1
            if event.get("success"):
                target["successful_calls"] += 1
            else:
                target["failed_calls"] += 1
            for field in token_fields:
                target[field] += _int(event.get(field))
            target["total_latency_ms"] += max(0.0, float(event.get("latency_ms", 0.0) or 0.0))

        billed = _int(event.get("total_tokens")) > 0
        if billed:
            summary["billed_calls"] += 1
            bucket["billed_calls"] += 1
        cost = event.get("estimated_cost_usd")
        if cost is None:
            if billed:
                summary["unpriced_billed_calls"] += 1
                bucket["unpriced_billed_calls"] += 1
        else:
            value = float(cost)
            summary["estimated_cost_usd"] += value
            bucket["estimated_cost_usd"] += value
        model = str(event.get("model", ""))
        if model and model not in bucket["models"]:
            bucket["models"].append(model)
        band = str(event.get("price_band", ""))
        if band and band not in bucket["price_bands"]:
            bucket["price_bands"].append(band)

    for target in [summary, *summary["providers"].values()]:
        target["total_latency_ms"] = round(float(target["total_latency_ms"]), 1)
        target["estimated_cost_usd"] = round(float(target["estimated_cost_usd"]), 10)
    return summary
