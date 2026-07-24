from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import requests


GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODEL = "openai/gpt-4.1"
PROMPT_VERSION = "academic-door-abstract-zh-v2"
NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)"
    r"(?P<percent_word>\s+(?:percent|per\s+cent))?"
    r"(?:st|nd|rd|th|s)?"
    r"(?![A-Za-z0-9_])"
    ,
    re.IGNORECASE,
)
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")


class TranslationError(RuntimeError):
    pass


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise TranslationError("Model response did not contain a JSON object")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as error:
            raise TranslationError("Model response contained invalid JSON") from error
    if not isinstance(data, dict):
        raise TranslationError("Model response must be a JSON object")
    return data


def _numbers(value: str) -> list[str]:
    values: list[str] = []
    for match in NUMBER_PATTERN.finditer(value):
        number = match.group("number")
        if match.group("percent_word") and not number.endswith("%"):
            number += "%"
        values.append(number)
    return values


def _source_hash(article: dict[str, Any]) -> str:
    source = f"{article.get('title_en', '')}\n{article.get('abstract_en', '')}"
    return sha256(source.encode("utf-8")).hexdigest()


def validate_translation(article: dict[str, Any], translated: dict[str, Any]) -> None:
    title_cn = str(translated.get("title_cn", "")).strip()
    abstract_cn = str(translated.get("abstract_cn", "")).strip()
    if not title_cn or not abstract_cn:
        raise TranslationError("Chinese title and abstract are both required")
    if not CJK_PATTERN.search(title_cn) or not CJK_PATTERN.search(abstract_cn):
        raise TranslationError("Translation must contain Chinese characters")
    if len(abstract_cn) < min(80, max(30, int(len(article["abstract_en"]) * 0.15))):
        raise TranslationError("Chinese abstract is suspiciously short")
    if "```" in title_cn or "```" in abstract_cn:
        raise TranslationError("Translation must not contain Markdown fences")
    source_numbers = Counter(
        _numbers(f"{article.get('title_en', '')}\n{article.get('abstract_en', '')}")
    )
    translated_numbers = Counter(_numbers(f"{title_cn}\n{abstract_cn}"))
    if source_numbers != translated_numbers:
        missing_numbers = list((source_numbers - translated_numbers).elements())
        added_numbers = list((translated_numbers - source_numbers).elements())
        details = []
        if missing_numbers:
            details.append("missing " + ", ".join(missing_numbers))
        if added_numbers:
            details.append("added " + ", ".join(added_numbers))
        raise TranslationError(
            "Translation changed numeric values: " + "; ".join(details)
        )


def _prompt(article: dict[str, Any]) -> list[dict[str, str]]:
    source = {
        "title_en": article["title_en"],
        "abstract_en": article["abstract_en"],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是经济学期刊的专业中英翻译。忠实翻译，不概括、不扩写、不评价。"
                "保留全部数字、比例、样本、方法、变量、结论方向和缩写；术语采用中文经济学文献常用表达。"
                "源摘要中的每一个阿拉伯数字必须原样保留，包括千位逗号、小数点、百分号和年份；"
                "不得把数字改写成中文数字、万、亿或年代简称。"
                "英文拼写的数字应翻译为中文文字，不得因此新增阿拉伯数字；"
                "译文不得添加源标题和摘要中不存在的阿拉伯数字。"
                "只返回严格 JSON，字段固定为 title_cn 和 abstract_cn，不使用 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(source, ensure_ascii=False),
        },
    ]


def request_translation(
    article: dict[str, Any],
    *,
    token: str,
    model: str = DEFAULT_MODEL,
    endpoint: str = GITHUB_MODELS_ENDPOINT,
    session: requests.Session | None = None,
    retries: int = 3,
    timeout: int = 90,
) -> dict[str, str]:
    if not token:
        raise TranslationError("GitHub Models token is required")
    client = session or requests.Session()
    payload = {
        "model": model,
        "temperature": 0,
        "messages": _prompt(article),
    }
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Academic-Door-Journals/1.0",
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            translated = _extract_json(content)
            validate_translation(article, translated)
            return {
                "title_cn": translated["title_cn"].strip(),
                "abstract_cn": translated["abstract_cn"].strip(),
            }
        except (requests.RequestException, KeyError, IndexError, TranslationError) as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise TranslationError(
        f"Translation failed after {retries} attempts: {last_error}"
    )


def translate_missing(
    issue: dict[str, Any],
    cache_path: Path,
    *,
    token: str | None = None,
    model: str | None = None,
    endpoint: str = GITHUB_MODELS_ENDPOINT,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    cache = (
        json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_path.exists()
        else {}
    )
    auth_token = token or os.environ.get("GITHUB_TOKEN", "")
    selected_model = model or os.environ.get("TRANSLATION_MODEL", DEFAULT_MODEL)
    translated_count = 0
    invalid_cache_count = 0
    upgraded_cache_count = 0
    failures: list[dict[str, str]] = []

    for article in issue["articles"]:
        doi = article.get("doi", "")
        if not doi or not article.get("abstract_en"):
            continue
        existing = cache.get(doi, {})
        source_hash = _source_hash(article)
        if existing.get("title_cn") and existing.get("abstract_cn"):
            try:
                validate_translation(article, existing)
                if (
                    existing.get("source_hash")
                    and existing.get("source_hash") != source_hash
                ):
                    raise TranslationError("Source title or abstract changed")
                if not existing.get("source_hash"):
                    existing["source_hash"] = source_hash
                    upgraded_cache_count += 1
                continue
            except TranslationError:
                invalid_cache_count += 1
        try:
            translated = request_translation(
                article,
                token=auth_token,
                model=selected_model,
                endpoint=endpoint,
                session=session,
            )
            cache[doi] = {
                **existing,
                **translated,
                "source_hash": source_hash,
                "translation": {
                    "provider": "github-models",
                    "model": selected_model,
                    "prompt_version": PROMPT_VERSION,
                    "translated_at": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                },
            }
            translated_count += 1
        except TranslationError as error:
            failures.append(
                {
                    "doi": doi,
                    "title_en": article.get("title_en", ""),
                    "error": str(error),
                }
            )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "journal_id": issue["journal_id"],
        "translated": translated_count,
        "invalid_cache_entries": invalid_cache_count,
        "upgraded_cache_entries": upgraded_cache_count,
        "failed": failures,
        "model": selected_model,
        "prompt_version": PROMPT_VERSION,
    }
