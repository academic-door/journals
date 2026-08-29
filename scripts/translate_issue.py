from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import requests
from collectors.article_types import has_official_no_abstract_exception


GITHUB_MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
GITHUB_MODELS_API_VERSION = "2026-03-10"
# Optional primary provider: set DEEPSEEK_API_KEY to prefer DeepSeek over
# GitHub Models (OpenAI-compatible chat completions API).
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"


def _deepseek_model() -> str:
    """Resolve the DeepSeek model, honoring the DEEPSEEK_MODEL environment
    override so operators can switch models (e.g. deepseek-reasoner) without
    code changes."""
    configured = os.environ.get("DEEPSEEK_MODEL", "").strip()
    return configured or DEEPSEEK_MODEL


GOOGLE_TRANSLATE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
GOOGLE_MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("GOOGLE_TRANSLATE_MIN_INTERVAL", "2.0")
)
_last_google_request_at = 0.0
DEFAULT_MODEL = "openai/gpt-4.1"
PROMPT_VERSION = "academic-door-abstract-zh-v2"
NUMBER_PATTERN = re.compile(
    # Attached alphanumeric terms are protected as complete tokens by
    # ``_protect_numbers``. Keep them outside this legacy numeric validator so
    # previously published translations remain backward-compatible.
    r"(?<![A-Za-z0-9_])"
    r"(?P<number>[+\-\u2212]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)"
    r"(?P<percent_word>\s+(?:percent|per\s+cent))?"
    r"(?:st|nd|rd|th|s)?"
    r"(?![A-Za-z0-9_])"
    ,
    re.IGNORECASE,
)
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
# A numeric sentinel is intentionally used because the numeric-protection layer
# guarantees that the translation service cannot rewrite it.
GOOGLE_SECTION_MARKER = "9876543210123456789"
NUMBER_WORDS_ZH = {
    "zero": "零",
    "one": "一",
    "two": "二",
    "three": "三",
    "four": "四",
    "five": "五",
    "six": "六",
    "seven": "七",
    "eight": "八",
    "nine": "九",
    "ten": "十",
    "eleven": "十一",
    "twelve": "十二",
    "thirteen": "十三",
    "fourteen": "十四",
    "fifteen": "十五",
    "sixteen": "十六",
    "seventeen": "十七",
    "eighteen": "十八",
    "nineteen": "十九",
    "twenty": "二十",
    "first": "第一",
    "second": "第二",
    "third": "第三",
    "fourth": "第四",
    "fifth": "第五",
    "sixth": "第六",
    "seventh": "第七",
    "eighth": "第八",
    "ninth": "第九",
    "tenth": "第十",
    "thirty": "三十",
    "forty": "四十",
    "fifty": "五十",
    "sixty": "六十",
    "seventy": "七十",
    "eighty": "八十",
    "ninety": "九十",
    "hundred": "百",
    "thousand": "千",
    "million": "百万",
    "billion": "十亿",
}
MONTH_WORDS_ZH = {
    "January": "一月",
    "February": "二月",
    "March": "三月",
    "April": "四月",
    "May": "五月",
    "June": "六月",
    "July": "七月",
    "August": "八月",
    "September": "九月",
    "October": "十月",
    "November": "十一月",
    "December": "十二月",
}
MONTH_EN_INDEX = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
CHINESE_MONTH_PATTERN = re.compile(
    r"(?<![一二三四五六七八九十])"
    r"(一月|二月|三月|四月|五月|六月|七月|八月|九月|十月|十一月|十二月)"
    r"(?![一二三四五六七八九十])"
)


def _month_numbers(value: str) -> list[str]:
    """Month names are numeric values; ``July 2006`` equals ``7月``."""
    numbers: list[str] = []
    for name, index in MONTH_EN_INDEX.items():
        month_with_year = re.compile(
            rf"\b{name}\b(?=\s+(?:19|20)\d{{2}}\b)",
            flags=re.IGNORECASE,
        )
        month_with_day_year = re.compile(
            rf"\b(?P<month>{name})\b\s+\d{{1,2}}(?:st|nd|rd|th)?\s*,?\s+(?:19|20)\d{{2}}\b",
            flags=re.IGNORECASE,
        )
        month_after_preposition = re.compile(
            rf"\b(?:in|from|through|until|between|during|as of|to)\s+(?P<month>{name})\b",
            flags=re.IGNORECASE,
        )
        seen_spans: set[tuple[int, int]] = set()
        for _match in month_with_year.finditer(value):
            span = (_match.start(), _match.end())
            if span not in seen_spans:
                seen_spans.add(span)
                numbers.append(str(index))
        for _match in month_with_day_year.finditer(value):
            span = _match.span("month")
            if span not in seen_spans:
                seen_spans.add(span)
                numbers.append(str(index))
        for _match in month_after_preposition.finditer(value):
            span = _match.span("month")
            if span not in seen_spans:
                seen_spans.add(span)
                numbers.append(str(index))
    for index, month_cn in enumerate(MONTH_WORDS_ZH.values(), start=1):
        for _match in CHINESE_MONTH_PATTERN.finditer(value):
            if _match.group(0) == month_cn:
                numbers.append(str(index))
    month_period_pattern = re.compile(
        r"\b(\d{4})M(0?[1-9]|1[0-2])\b",
        flags=re.IGNORECASE,
    )
    for _match in month_period_pattern.finditer(value):
        numbers.append(_match.group(1))
        numbers.append(str(int(_match.group(2))))
    return numbers


NUMBER_WORD_VALUES = {
    word: index
    for index, word in enumerate(
        (
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        )
    )
}
NUMBER_WORD_VALUES.update(
    {
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
    }
)
NUMBER_VALUES_ZH = {
    value: NUMBER_WORDS_ZH[word]
    for word, value in NUMBER_WORD_VALUES.items()
}
NUMBER_WORD_SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}
ENGLISH_NUMBER_WORDS = [*NUMBER_WORD_VALUES, *NUMBER_WORD_SCALES, "and"]
ENGLISH_NUMBER_TOKEN_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(sorted(ENGLISH_NUMBER_WORDS, key=len, reverse=True))
    + r")(?:[-\s]+(?:"
    + "|".join(sorted(ENGLISH_NUMBER_WORDS, key=len, reverse=True))
    + r"))*\b",
    re.IGNORECASE,
)


def _parse_english_number_phrase(phrase: str) -> int | None:
    """Parse a conservative English number phrase into its numeric value."""

    tokens = [
        token
        for token in re.split(r"[-\s]+", phrase.casefold())
        if token and token != "and"
    ]
    if not tokens:
        return None
    total = 0
    current = 0
    for token in tokens:
        if token in NUMBER_WORD_VALUES:
            current += NUMBER_WORD_VALUES[token]
        elif token in NUMBER_WORD_SCALES:
            current = (current or 1) * NUMBER_WORD_SCALES[token]
            total += current
            current = 0
        else:
            return None
    return total + current


def _written_number_values(value: str) -> list[str]:
    """Return unambiguous written-number quantities from English text."""

    # Treat ``a hundred`` like ``one hundred`` while keeping ``half a
    # million`` on its dedicated natural-language path below.
    searchable = re.sub(
        r"\bhalf\s+a\s+(million|billion)\b",
        r"half \1",
        value,
        flags=re.IGNORECASE,
    )
    searchable = re.sub(
        r"\b(?:a|an)\s+(?=(?:hundred|thousand|million|billion)\b)",
        "one ",
        searchable,
        flags=re.IGNORECASE,
    )
    values: list[str] = []
    for match in ENGLISH_NUMBER_TOKEN_PATTERN.finditer(searchable):
        phrase = match.group(0)
        parsed = _parse_english_number_phrase(phrase)
        if parsed is None:
            continue
        following = searchable[match.end() :]
        percent = re.match(r"\s+percent|\s+per\s+cent", following, re.IGNORECASE)
        percent_range = re.match(
            r"\s+to\s+(?:" + ENGLISH_NUMBER_TOKEN_PATTERN.pattern + r")\s+"
            r"(?:percent|per\s+cent)\b",
            following,
            flags=re.IGNORECASE,
        )
        has_scale = any(
            token in NUMBER_WORD_SCALES
            for token in phrase.casefold().replace("-", " ").split()
        )
        has_cardinal = any(
            token in NUMBER_WORD_VALUES
            for token in phrase.casefold().replace("-", " ").split()
        )
        # A bare unit such as the ``billion`` in ``$14.5 billion`` is not a
        # written number.  Only treat scale phrases as quantities when they
        # contain an actual cardinal word (e.g. ``two thousand and ten``).
        if percent or percent_range or (has_scale and has_cardinal) or "-" in phrase:
            values.append(f"{parsed}%" if percent or percent_range else str(parsed))
    return values
CENTURY_ORDINAL_VALUES = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
    "twenty-first": 21,
}


class TranslationError(RuntimeError):
    pass


TERMINAL_PROVIDER_STATUS_CODES = {400, 401, 403, 404, 410, 422}


class ProviderUnavailableError(TranslationError):
    def __init__(self, provider: str, status_code: int) -> None:
        self.provider = provider
        self.status_code = status_code
        super().__init__(
            f"{provider} unavailable for this run (HTTP {status_code})"
        )


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


# Numbers that label studies, figures, tables, models, etc. are identifiers,
# not reported numeric results. Translators legitimately render "Study 2" as
# "研究二" or drop the digit, so these must not trip the numeric fidelity gate.
IDENTIFIER_EN = re.compile(
    r"(?i)(?<![A-Za-z])"
    r"(?:study|experiment|figure|table|model|section|appendix|equation|"
    r"hypothesis|column|row|part|step|panel|scenario|test|trial|wave|round|stage|phase|studies|"
    r"cohort|group|sample|survey|task|condition|session|block|version|"
    r"chapter|bill|article|act|title|clause|provision|rule|law|regulation|"
    r"specification)\s*$"
)
# Chinese label nouns that fuse with ordinals ("实验2" = Experiment 2,
# "第2轮" = Round 2). Longer words are matched first; single-character labels
# must not be the tail of a longer verb or noun ("进行2.1%" is a statistic,
# not a row/column label; "around" must not match "round").
IDENTIFIER_CJK_WORDS = [
    "实验",
    "研究",
    "图表",
    "模型",
    "步骤",
    "阶段",
    "测试",
    "试验",
    "版本",
    "样本",
    "调查",
    "任务",
    "条件",
    "会话",
    "章节",
    "附录",
    "方程",
    "假设",
    "场景",
    "轮次",
    "图",
    "表",
    "行",
    "列",
    "轮",
    "组",
    "版",
    "步",
    "项",
    "条",
    "块",
    "章",
    "节",
    "期",
    "批",
    "号",
    "层",
]
IDENTIFIER_CJK_SORTED = sorted(IDENTIFIER_CJK_WORDS, key=len, reverse=True)


def _cjk_identifier_label(prefix: str) -> str | None:
    """Return the Chinese identifier label directly preceding the number,
    allowing whitespace between the label and the ordinal ("实验 1")."""
    stripped = prefix.rstrip(' \t')
    for label in IDENTIFIER_CJK_SORTED:
        if not stripped.endswith(label):
            continue
        before = stripped[: len(stripped) - len(label)]
        if len(label) == 1 and before and CJK_PATTERN.match(before[-1]):
            # Single-char labels are often the tail of a longer word
            # ("进行2.1%"). Require a boundary before them.
            continue
        return label
    return None


def _is_identifier_number(value: str, match: re.Match[str]) -> bool:
    prefix = value[: match.start()]
    if IDENTIFIER_EN.search(prefix):
        return True
    digits = re.sub(r"[^0-9]", "", str(match.group("number")))
    if _cjk_identifier_label(prefix) is not None:
        # Chinese label nouns fuse with the ordinal ("研究2" = Study 2), but
        # the same noun is also a verb before years ("研究1959古巴革命" =
        # studying the 1959 Cuban Revolution). Labels are small ordinals;
        # 4-digit years and statistics must keep counting.
        return len(digits) <= 3
    if prefix.rstrip(' \t').endswith("第"):
        # "第2轮" / "第3阶段": the ordinal marker precedes the digit and the
        # label noun follows it. Same small-ordinal rule as above.
        rest = value[match.end() :].lstrip()
        return (
            any(rest.startswith(label) for label in IDENTIFIER_CJK_SORTED)
            and len(digits) <= 3
        )
    return False


CURRENCY_PREFIX_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?:EUR|USD|GBP|CNY|JPY|CHF|CAD|AUD|HKD|SGD|NZD|SEK|NOK|DKK|PLN|RMB|NTD|TWD)"
    r"\s*(?P<amount>[+\-\u2212]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
)

# Quantities written without a space between the number and a compact unit are
# common in economics abstracts.  The generic matcher intentionally excludes
# attached alphanumeric identifiers, so capture the small, explicit unit set
# here.  Only the quantity is audited: the exponent in ``1km2`` is part of the
# unit and may naturally become ``平方公里`` in Chinese.
ATTACHED_QUANTITY_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"(?P<number>[+\-\u2212]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?:pp|bps?|km(?:2|3|²|³)?|m(?:2|3|²|³)|cm(?:2|3|²|³)|"
    r"mm(?:2|3|²|³)|kg|mg|ha|mph|kph|US\$)(?![A-Za-z0-9_])"
)


def _canonical_number(number: str) -> str:
    """Normalize a matched number for multiset comparison.

    Thousand separators (``5,503``) and currency-prefixed amounts
    (``EUR190``) are the same value whether the translation keeps the comma
    or writes ``5503``/``190欧元``; strip commas and the currency prefix so
    both sides compare equal.
    """
    return number.replace("\u2212", "-").lstrip("+").replace(",", "")


def _numbers(value: str) -> list[str]:
    values: list[str] = []
    protected_spans: list[tuple[int, int]] = []
    for match in CURRENCY_PREFIX_PATTERN.finditer(value):
        protected_spans.append(match.span())
        values.append(_canonical_number(match.group("amount")))
    for match in ATTACHED_QUANTITY_PATTERN.finditer(value):
        protected_spans.append(match.span())
        values.append(_canonical_number(match.group("number")))
    for match in NUMBER_PATTERN.finditer(value):
        # Currency-prefixed amounts and attached quantities were already
        # captured above; do not count their digits a second time.
        if any(start <= match.start() < end for start, end in protected_spans):
            continue
        # Unit exponents such as ``year−1`` are commonly rendered as “每年” in
        # Chinese. They describe a denominator, not a reported numeric result.
        if (
            match.start() > 1
            and value[match.start() - 1] == "−"
            and value[match.start() - 2].isalpha()
        ):
            continue
        if _is_identifier_number(value, match):
            continue
        number = _canonical_number(match.group("number"))
        if match.group("percent_word") and not number.endswith("%"):
            number += "%"
        elif (
            not number.endswith("%")
            and value[match.end() :].lstrip().startswith("%")
        ):
            number += "%"
        values.append(number)
    return values


def _identifier_numbers(value: str) -> list[str]:
    """Numbers that the numeric validator skips because they are section,
    table or figure identifiers rather than reported data values.

    The source side skips them (``Section 5503``), so a translation that
    renders the identifier with the Arabic digit (``第5503条``) must get the
    same exemption instead of being flagged as an invented number.
    """
    return [
        _canonical_number(match.group("number"))
        for match in NUMBER_PATTERN.finditer(value)
        if _is_identifier_number(value, match)
    ]


def _source_hash(article: dict[str, Any]) -> str:
    source = f"{article.get('title_en', '')}\n{article.get('abstract_en', '')}"
    return sha256(source.encode("utf-8")).hexdigest()


def _alpha_index(index: int) -> str:
    value = ""
    current = index
    while True:
        current, remainder = divmod(current, 26)
        value = chr(ord("A") + remainder) + value
        if current == 0:
            return value
        current -= 1


def _protect_numbers(
    value: str, *, placeholder_prefix: str = "", opaque: bool = False
) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}
    protected_ranges: dict[str, str] = {}
    protected_alphanumeric: dict[str, str] = {}

    def protected_token(number: str) -> str:
        # Google Translate may silently omit opaque alphabetic placeholders
        # when they stand where a quantity belongs in a sentence. Keep the
        # actual quantity visible to the translator and only wrap it in a
        # stable delimiter. This preserves both sentence meaning and the exact
        # source value for the numeric quality gate.
        # DeepSeek may paraphrase visible placeholders such as ``[[4]]``.
        # Use opaque tokens during the model call and restore the exact source
        # value after translation. Prefixes prevent title/abstract collisions.
        if opaque:
            token = (
                f"⟦ADNUM_{placeholder_prefix}_{_alpha_index(len(replacements))}⟧"
            )
        else:
            token = f"[[{number}]]"
        replacements[token] = number
        return token

    # Keep scientific abbreviations and currency-prefixed amounts together.
    # Splitting ``CO2`` into ``CO[[2]]`` lets machine translation move the
    # digit away from the abbreviation while still passing a numeric count.
    alphanumeric_pattern = re.compile(
        r"(?<![A-Za-z0-9_])[A-Za-z]{1,8}\d+(?:\.\d+)?(?![A-Za-z0-9_])"
    )

    def replace_alphanumeric(match: re.Match[str]) -> str:
        token = f"ATGALNUM{_alpha_index(len(protected_alphanumeric))}END"
        protected_alphanumeric[token] = match.group(0)
        return token

    value = alphanumeric_pattern.sub(replace_alphanumeric, value)

    range_pattern = re.compile(
        r"(?<![A-Za-z0-9_])"
        r"(?P<low>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
        r"\s*(?:[-–—]|每)\s*"
        r"(?P<high>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)"
        r"(?P<percent_word>\s+(?:percent|per\s+cent))?"
        r"(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )

    def replace_range(match: re.Match[str]) -> str:
        high = match.group("high")
        if match.group("percent_word") and not high.endswith("%"):
            high += "%"
        number = f"{match.group('low')}-{high}"
        token = f"ATGRANGE{_alpha_index(len(protected_ranges))}END"
        protected_ranges[token] = number
        return token

    value = range_pattern.sub(replace_range, value)

    def replace(match: re.Match[str]) -> str:
        number = match.group("number")
        if match.group("percent_word") and not number.endswith("%"):
            number += "%"
        return protected_token(number)

    protected = NUMBER_PATTERN.sub(replace, value)
    for range_token, number in protected_ranges.items():
        protected = protected.replace(range_token, protected_token(number))
    for alphanumeric_token, source_token in protected_alphanumeric.items():
        protected = protected.replace(
            alphanumeric_token, protected_token(source_token)
        )
    month_pattern = re.compile(
        r"\b(" + "|".join(MONTH_WORDS_ZH) + r")\b(?=\s+\[\[[^\]]+\]\])"
    )

    def replace_month(match: re.Match[str]) -> str:
        return protected_token(MONTH_WORDS_ZH[match.group(0)])

    protected = month_pattern.sub(replace_month, protected)
    # Let the translator handle written-out number words. Replacing ``two``
    # with bare ``[[二]]`` produced phrases such as ``二实验组`` rather than
    # the natural ``两个实验组``.
    return protected, replacements


def _restore_numbers(value: str, replacements: dict[str, str]) -> str:
    restored = value
    # Google occasionally fuses a protected placeholder with the next English
    # word (``[[2019]] MFP`` becomes ``[[2019]]MFP``). Without a separator the
    # numeric gate cannot see the restored digit. Reinsert the space only when
    # the placeholder is directly followed by an ASCII letter.
    restored = re.sub(r"\]\](?=[A-Za-z])", "]] ", restored)
    for token, number in replacements.items():
        restored = re.sub(re.escape(token), number, restored, flags=re.IGNORECASE)

    # Google occasionally preserves the brackets and digits but drops only a
    # percent sign (for example ``[[27%]]`` becomes ``[[27]]``). Recover that
    # formatting only when the remaining bracketed value has exactly one
    # source match after removing the percent sign. Ambiguous or changed
    # values remain untouched and are still rejected by the quality gate.
    def restore_relaxed(match: re.Match[str]) -> str:
        observed = match.group(1).strip()
        observed_base = re.sub(r"\s+", "", observed).rstrip("%")
        candidates = {
            number
            for number in replacements.values()
            if re.sub(r"\s+", "", number).rstrip("%") == observed_base
        }
        return next(iter(candidates)) if len(candidates) == 1 else match.group(0)

    restored = re.sub(r"\[\[([^\]]+)\]\]", restore_relaxed, restored)
    return restored


def _canonicalize_arabic_numbers(source: str, translated: str) -> str:
    source_numbers = _numbers(source)
    translated_matches = list(NUMBER_PATTERN.finditer(translated))
    if len(source_numbers) != len(translated_matches):
        return translated
    # When the translation already contains exactly the source numbers
    # (as a multiset), a positional rewrite would corrupt reordered Chinese
    # sentences such as "(5-7 years) remains about 2% lower", where the range
    # appears before the percentage. Only rewrite when values actually drifted.
    if Counter(_numbers(translated)) == Counter(source_numbers):
        return translated
    values = iter(source_numbers)
    return NUMBER_PATTERN.sub(lambda _match: next(values), translated)


def _repair_google_artifacts(source: str, translated: str) -> str:
    """Patch a few recurring Google fallback glitches using the source text.

    Google occasionally fuses percentage phrases with age ranges or drops the
    percent sign from phrases like "equal to 27 percent of family income".
    These repairs are intentionally narrow and source-driven so they only touch
    the malformed patterns we can identify with confidence.
    """

    repaired = translated

    age_range = re.search(
        r"\bat ages?\s+(\d+)\s*[–-]\s*(\d+)\b", source, flags=re.IGNORECASE
    )
    if age_range:
        low, high = age_range.groups()
        repaired = re.sub(
            r"在\s*(?:\d+%\s*-?\s*\d+|\d+\s*-\s*\d+)岁时",
            f"在{low}至{high}岁时",
            repaired,
            count=1,
        )
        repaired = re.sub(
            r"在\s*\d+岁时",
            f"在{low}至{high}岁时",
            repaired,
            count=1,
        )

    family_income = re.search(
        r"equal to\s+([0-9,]+(?:\.\d+)?)\s+percent of family income",
        source,
        flags=re.IGNORECASE,
    )
    if family_income:
        pct = family_income.group(1)
        repaired = re.sub(
            r"相当于家庭收入的\s*[0-9,]+(?:\.\d+)?",
            f"相当于家庭收入的{pct}%",
            repaired,
            count=1,
        )

    source_range = re.search(
        r"\b(\d+)\s*(?:[-–—]|每)\s*(\d+)\b", source
    )
    if source_range:
        low, high = source_range.group(1), source_range.group(2)
        # Google occasionally renders a protected range as only "-high".
        # The low bound may legitimately appear elsewhere in the translation
        # (e.g. "equivalent to 1000 scholars"), so only require the dangling
        # "-high" form; a source-backed low-high range makes that form a
        # corrupt range rendering, not a real negative number.
        if re.search(rf"(?<!\d)[-\u2212]{re.escape(high)}(?!\d)", repaired):
            repaired = re.sub(
                rf"(?<!\d)[-\u2212]{re.escape(high)}(?!\d)",
                f"{low}-{high}",
                repaired,
                count=1,
            )

    democracy_count = re.search(
        r"\bin\s+([0-9,]+)\s+democrac", source, flags=re.IGNORECASE
    )
    if democracy_count:
        count = democracy_count.group(1)
        if not re.search(rf"(?<!\d){re.escape(count)}(?!\d)", repaired):
            repaired = re.sub(
                r"(?<=\d)民主国家",
                f"的{count}个民主国家",
                repaired,
                count=1,
            )

    through_age = re.search(
        r"\bthrough age\s+(\d+)\b", source, flags=re.IGNORECASE
    )
    if through_age:
        age = through_age.group(1)
        repaired = re.sub(
            r"(?:年龄|岁期间)\s*\d+",
            f"至{age}岁",
            repaired,
            count=1,
        )
        repaired = re.sub(
            rf"，而在整个至{re.escape(age)}岁期间福利一直较少",
            f"，较小的福利持续至{age}岁",
            repaired,
            count=1,
        )
        repaired = re.sub(
            r"持续(?:through|至|到)?\s*\d+岁",
            f"持续至{age}岁",
            repaired,
            count=1,
        )

    return repaired


_ZH_DIGITS = "零一二三四五六七八九"


def _zh_integer(value: int) -> str:
    if value < 10:
        return _ZH_DIGITS[value]
    if value < 20:
        return "十" + (_ZH_DIGITS[value - 10] if value > 10 else "")
    if value < 100:
        tens, ones = divmod(value, 10)
        return _ZH_DIGITS[tens] + "十" + (_ZH_DIGITS[ones] if ones else "")
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        base = _ZH_DIGITS[hundreds] + "百"
        if not rest:
            return base
        if rest < 10:
            return base + "零" + _ZH_DIGITS[rest]
        return base + _zh_integer(rest)
    thousands, rest = divmod(value, 1000)
    base = _ZH_DIGITS[thousands] + "千"
    if not rest:
        return base
    if rest < 100:
        return base + "零" + _zh_integer(rest)
    return base + _zh_integer(rest)



CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
CN_NUMERAL_PATTERN = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")
CN_NUMERAL_SPACE_PATTERN = re.compile(
    r"(?<=[零〇一二两三四五六七八九十百千万亿])\s+"
    r"(?=[零〇一二两三四五六七八九十百千万亿])"
)


def _collapse_chinese_numeral_spaces(value: str) -> str:
    """Remove translator-inserted spaces inside Chinese numerals only."""

    return CN_NUMERAL_SPACE_PATTERN.sub("", value)


def _parse_chinese_numeral(value: str) -> int:
    """Parse a Chinese numeral sequence (up to 亿) into an integer.

    Handles standard forms such as 八十=80, 一百二十三=123, 二〇二五=2025
    and 二十万=200000.
    """
    if not any(char in CN_UNITS for char in value):
        # Pure digit sequence (e.g. 二〇二五 for 2025): concatenate digits.
        result = 0
        for char in value:
            result = result * 10 + CN_DIGITS[char]
        return result
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
        elif char in CN_UNITS:
            unit = CN_UNITS[char]
            if unit < 10000:
                section += (number or 1) * unit
                number = 0
            else:
                total += (section + number) * unit
                section = 0
                number = 0
    return total + section + number


def _canonicalize_chinese_numerals(source: str, translated: str) -> str:
    """Convert Chinese-written numerals back to Arabic when the source has the
    same value as an Arabic number.

    Chinese translations routinely render source Arabic numbers as Chinese
    numerals (e.g. ``80`` -> ``八十``). Those are the same value, not invented
    numbers; converting only the values that actually occur as Arabic numerals
    in the source lets the strict multiset validator compare numeric content
    instead of script form, without disturbing legitimate Chinese renderings
    of English number words (e.g. ``half a million`` -> ``五十万``).
    """
    source_numbers = set(_numbers(source) + _written_number_values(source))

    def replace_percent(match: re.Match[str]) -> str:
        value = _parse_chinese_numeral(match.group(1))
        rendered = f"{value}%"
        if rendered in source_numbers:
            return rendered
        return match.group(0)

    normalized = re.sub(
        r"百分之(" + CN_NUMERAL_PATTERN.pattern + r")",
        replace_percent,
        _collapse_chinese_numeral_spaces(translated),
    )

    def replace_numeral(match: re.Match[str]) -> str:
        sequence = match.group(0)
        if len(sequence) < 2:
            # Single Chinese digits usually render English count words
            # (``two types`` -> ``两种``) and are ambiguous with data values;
            # leave them alone.
            return sequence
        value = _parse_chinese_numeral(sequence)
        if str(value) in source_numbers:
            return str(value)
        return sequence

    return CN_NUMERAL_PATTERN.sub(replace_numeral, normalized)



def _normalize_written_number_translations(source: str, translated: str) -> str:
    """Normalize valid Chinese renderings of English month/number words.

    This prevents a translated ``December`` -> ``12月`` or ``three percent`` ->
    ``3%`` from being mistaken for an invented Arabic number. Source Arabic
    numbers remain subject to exact multiset validation. Chinese-written
    numerals (``八十`` for 80) are canonicalized back to Arabic first so they
    compare equal to the source value.
    """

    normalized = _canonicalize_chinese_numerals(
        source,
        _collapse_chinese_numeral_spaces(translated),
    )
    source_numbers = Counter(_numbers(source) + _written_number_values(source))

    # Models commonly render ``10 percent`` as ``10个百分点`` or ``10百分比``.
    # Preserve the natural Chinese wording unless the source actually carries
    # that same percentage; then normalize only for the strict multiset audit.
    def normalize_arabic_percent_term(match: re.Match[str]) -> str:
        number = _canonical_number(match.group("number")) + "%"
        return number if source_numbers.get(number, 0) else match.group(0)

    normalized = re.sub(
        r"(?<!\d)(?P<number>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
        r"(?:个百分点|百分比)",
        normalize_arabic_percent_term,
        normalized,
    )
    century_pattern = re.compile(
        r"\b(" + "|".join(
            sorted(CENTURY_ORDINAL_VALUES, key=len, reverse=True)
        ) + r")\s+century\b",
        re.IGNORECASE,
    )
    for match in century_pattern.finditer(source):
        value = CENTURY_ORDINAL_VALUES[match.group(1).lower()]
        normalized, _changed = re.subn(
            rf"(?<!\d){value}\s*世纪",
            _zh_integer(value) + "世纪",
            normalized,
            count=1,
        )
    month_abbreviations = {
        "jan": "January", "feb": "February", "mar": "March", "apr": "April",
        "may": "May", "jun": "June", "jul": "July", "aug": "August",
        "sep": "September", "sept": "September", "oct": "October",
        "nov": "November", "dec": "December",
    }
    for month_index, (month, month_cn) in enumerate(MONTH_WORDS_ZH.items(), start=1):
        source_count = len(re.findall(rf"\b{month}\b", source, flags=re.IGNORECASE))
        for alias, full in month_abbreviations.items():
            if full.casefold() == month.casefold():
                source_count += len(
                    re.findall(rf"\b{re.escape(alias)}\.?\b", source, flags=re.IGNORECASE)
                )
        for _ in range(source_count):
            normalized, changed = re.subn(
                rf"(?<!\d){month_index}\s*月",
                month_cn,
                normalized,
                count=1,
            )
            if not changed:
                break

    words = "|".join(NUMBER_WORD_VALUES)
    percent_pattern = re.compile(
        rf"\b(?P<low>{words})(?:\s+to\s+(?P<high>{words}))?"
        r"\s+(?:percent|per\s+cent)\b",
        re.IGNORECASE,
    )
    written_percent_values: list[int] = []
    for match in percent_pattern.finditer(source):
        written_percent_values.append(NUMBER_WORD_VALUES[match.group("low").lower()])
        if match.group("high"):
            written_percent_values.append(
                NUMBER_WORD_VALUES[match.group("high").lower()]
            )
    for value in written_percent_values:
        normalized, _changed = re.subn(
            rf"(?<![\d.]){value}\s*[%％](?!\d)",
            f"百分之{NUMBER_VALUES_ZH[value]}",
            normalized,
            count=1,
        )
    for value in _written_number_values(source):
        if not value.endswith("%"):
            continue
        amount = int(value[:-1])
        normalized, _changed = re.subn(
            rf"(?<![\d.]){amount}\s*[%％](?!\d)",
            f"百分之{_zh_integer(amount)}",
            normalized,
            count=1,
        )
    word_pattern = re.compile(
        r"\b(" + "|".join(sorted(NUMBER_WORD_VALUES, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )
    half_unit = re.search(
        r"\bhalf\s+a\s+(million|billion)\b",
        source,
        flags=re.IGNORECASE,
    )
    if half_unit:
        unit = half_unit.group(1).lower()
        if unit == "million":
            # half a million = 500,000; Google renders it as 50万.
            normalized, _changed = re.subn(
                r"(?<![\d])50\s*万",
                "五十万",
                normalized,
                count=1,
            )
        elif unit == "billion":
            # half a billion = 500,000,000; Google renders it as 5亿.
            normalized, _changed = re.subn(
                r"(?<![\d])5\s*亿",
                "五亿",
                normalized,
                count=1,
            )
    unit_word = re.search(
        r"\b(?:a|one)\s+(thousand|million|billion)\b",
        source,
        flags=re.IGNORECASE,
    )
    if unit_word:
        unit = unit_word.group(1).lower()
        unit_zh = {"thousand": "一千", "million": "一百万", "billion": "十亿"}[unit]
        unit_value = {"thousand": 1000, "million": 1000000, "billion": 1000000000}[unit]
        unit_patterns = [rf"(?<!\d){unit_value}(?!\d)"]
        if unit == "thousand":
            unit_patterns.append(r"(?<!\d)1\s*千(?!\d)")
        for unit_pattern in unit_patterns:
            normalized, _changed = re.subn(
                unit_pattern,
                unit_zh,
                normalized,
                count=1,
            )
            if _changed:
                break

    bn_amount = re.search(r"\$?(\d+)\s*bn\b", source, flags=re.IGNORECASE)
    if bn_amount:
        # $20bn = 200亿; normalize the translator's 200亿 to 二百亿 so the
        # Arabic digit does not count as an invented number.
        amount = int(bn_amount.group(1))
        target = amount * 10
        if re.search(rf"(?<!\d){target}\s*亿", normalized):
            normalized, _changed = re.subn(
                rf"(?<!\d){target}\s*亿",
                _zh_integer(target) + "亿",
                normalized,
                count=1,
            )

    quarter_match = re.search(r"\b(\d{4})\s*Q([1-4])\b", source, re.IGNORECASE)
    if quarter_match:
        year = quarter_match.group(1)
        zh_quarter = {"1": "一", "2": "二", "3": "三", "4": "四"}[quarter_match.group(2)]
        normalized, _changed = re.subn(
            rf"(?<![\d]){year}\s*年\s*第{zh_quarter}季度",
            f"{year}Q{quarter_match.group(2)}",
            normalized,
            count=1,
        )
    source_digit_counts = source_numbers
    for match in word_pattern.finditer(source):
        word = match.group(0).lower()
        value = NUMBER_WORD_VALUES[word]
        digit_pattern = re.compile(
            rf"(?<![\d×xX*.,%％(（A-Za-z]){value}(?![\d×xX*.,%％)）A-Za-z])"
        )
        if len(digit_pattern.findall(normalized)) <= source_digit_counts.get(
            str(value), 0
        ):
            # The digit already exists in the source as an Arabic numeral
            # (e.g. data value 3 in "from 3 to -1 percent"), so a written
            # "three" elsewhere must not rewrite it.
            continue
        normalized, _changed = re.subn(
            digit_pattern,
            NUMBER_WORDS_ZH[word],
            normalized,
            count=1,
        )
    return normalized


def _chinese_percent_numbers(value: str) -> list[str]:
    """Extract Chinese percentage values for the strict numeric audit."""

    normalized = _collapse_chinese_numeral_spaces(value)
    values = [
        f"{_parse_chinese_numeral(match.group(1))}%"
        for match in re.finditer(
            r"百分之(" + CN_NUMERAL_PATTERN.pattern + r")", normalized
        )
    ]
    for match in re.finditer(
        r"(?<!百分之)(" + CN_NUMERAL_PATTERN.pattern + r")\s*"
        r"(?:到|至)\s*(" + CN_NUMERAL_PATTERN.pattern + r")\s*个百分点",
        normalized,
    ):
        values.extend(
            [
                f"{_parse_chinese_numeral(match.group(1))}%",
                f"{_parse_chinese_numeral(match.group(2))}%",
            ]
        )
    for match in re.finditer(
        r"(?<!百分之)(?<![到至])(" + CN_NUMERAL_PATTERN.pattern + r")\s*"
        r"(?:个)?百分点",
        normalized,
    ):
        values.append(f"{_parse_chinese_numeral(match.group(1))}%")
    for match in re.finditer(
        r"(?<!百分之)(" + CN_NUMERAL_PATTERN.pattern + r")\s*百分比",
        normalized,
    ):
        values.append(f"{_parse_chinese_numeral(match.group(1))}%")
    return values


def _translation_numeric_multiset(source: str, translated: str) -> Counter[str]:
    """Return translated numeric values in the source's numeric notation."""

    source_numbers = Counter(
        _numbers(source)
        + _month_numbers(source)
        + _written_number_values(source)
    )
    normalized = _canonicalize_chinese_numerals(
        source,
        _normalize_written_number_translations(source, translated),
    )
    translated_numbers = Counter(
        _numbers(normalized)
        + _month_numbers(normalized)
    )
    for value in _chinese_percent_numbers(normalized):
        # ``百分点`` is a normal Chinese rendering of a source percentage in
        # this corpus. If the source uses a bare number for percentage points,
        # retain that source notation instead of inventing a percent sign.
        target = value
        bare = value[:-1]
        if not source_numbers.get(value) and source_numbers.get(bare):
            target = bare
        translated_numbers[target] += 1
    return translated_numbers


def validate_translation(article: dict[str, Any], translated: dict[str, Any]) -> None:
    title_cn = str(translated.get("title_cn", "")).strip()
    abstract_cn = str(translated.get("abstract_cn", "")).strip()
    comment_without_abstract = (
        article.get("article_type") == "comment"
        and not article.get("abstract_en")
    )
    official_no_abstract = has_official_no_abstract_exception(article)
    if not title_cn or (
        not abstract_cn
        and not comment_without_abstract
        and not official_no_abstract
    ):
        raise TranslationError("Chinese title and abstract are both required")
    if not CJK_PATTERN.search(title_cn) or (
        abstract_cn and not CJK_PATTERN.search(abstract_cn)
    ):
        raise TranslationError("Translation must contain Chinese characters")
    minimum = (
        0
        if official_no_abstract
        else 10
        if article.get("article_type") == "comment"
        else min(80, max(30, int(len(article["abstract_en"]) * 0.15)))
    )
    if not comment_without_abstract and not official_no_abstract and len(abstract_cn) < minimum:
        raise TranslationError("Chinese abstract is suspiciously short")
    if "```" in title_cn or "```" in abstract_cn:
        raise TranslationError("Translation must not contain Markdown fences")
    source_text = f"{article.get('title_en', '')}\n{article.get('abstract_en', '')}"
    translated_text = f"{title_cn}\n{abstract_cn}"
    source_numbers = Counter(
        _numbers(source_text)
        + _month_numbers(source_text)
        + _written_number_values(source_text)
    )
    translated_numbers = _translation_numeric_multiset(
        source_text,
        translated_text,
    )
    # Identifier labels (Section 5503, Table 2, 第5503条) are not data
    # values: exempt the source's identifier numbers on the translation side
    # too, so rendering them with Arabic digits is not flagged as invented.
    for identifier_number in _identifier_numbers(source_text):
        # Only exempt a translation-side occurrence when the translation has
        # more of that value than the source's actual data count. When the
        # same value appears both as an identifier and as a data number
        # (e.g. "Study 3" plus "Studies 2 and 3"), the data occurrence must
        # keep counting; an unconditional subtraction would swallow it.
        if translated_numbers[identifier_number] > source_numbers[identifier_number]:
            translated_numbers[identifier_number] -= 1
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
                "如果源文本包含 ⟦ADNUM_...⟧ 占位符，必须逐字保留每个占位符，不得翻译、删除或改写；"
                "不得把数字改写成中文数字、万、亿或年代简称。"
                "英文拼写的数字应翻译为中文文字，不得因此新增阿拉伯数字；"
                "译文不得添加源标题和摘要中不存在的阿拉伯数字。"
                "只返回严格 JSON，字段固定为 title_cn 和 abstract_cn，不使用 Markdown。"
                '输出格式示例：{"title_cn":"中文标题","abstract_cn":"中文摘要"}。'
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
    provider_name: str = "github-models",
    protect_numbers: bool = False,
    max_tokens: int | None = None,
    json_output: bool = False,
    disable_thinking: bool = False,
) -> dict[str, str]:
    if not token:
        raise TranslationError(f"{provider_name} token is required")
    client = session or requests.Session()
    # Models usually preserve meaningful source numbers better than opaque
    # placeholders, but some models (e.g. DeepSeek) paraphrase or merge digits.
    # When protect_numbers is on, numbers are sent as [[...]] placeholders and
    # restored after translation, exactly like the Google fallback path.
    number_replacements: dict[str, str] = {}
    prompt_article = article
    if protect_numbers:
        prompt_article = dict(article)
        protected_title, title_replacements = _protect_numbers(
            str(article.get("title_en", "")), placeholder_prefix="T", opaque=protect_numbers
        )
        protected_abstract, abstract_replacements = _protect_numbers(
            str(article.get("abstract_en", "")), placeholder_prefix="A", opaque=protect_numbers
        )
        prompt_article["title_en"] = protected_title
        prompt_article["abstract_en"] = protected_abstract
        number_replacements = {**title_replacements, **abstract_replacements}
    payload = {
        "model": model,
        "temperature": 0,
        "messages": _prompt(prompt_article),
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_output:
        payload["response_format"] = {"type": "json_object"}
    if disable_thinking:
        payload["thinking"] = {"type": "disabled"}
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Academic-Door-Journals/1.0",
    }
    if provider_name == "github-models":
        headers["X-GitHub-Api-Version"] = GITHUB_MODELS_API_VERSION
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
            title_cn = str(translated.get("title_cn", ""))
            abstract_cn = str(translated.get("abstract_cn", ""))
            if protect_numbers:
                title_cn = _restore_numbers(title_cn, number_replacements)
                abstract_cn = _restore_numbers(abstract_cn, number_replacements)
                if re.search(r"(?:\[\[[^\]]+\]\]|⟦ADNUM_[^⟧]+⟧)", title_cn + abstract_cn):
                    raise TranslationError(
                        f"{provider_name} did not preserve numeric placeholders"
                    )
            translated = {
                "title_cn": _canonicalize_arabic_numbers(
                    article["title_en"],
                    _normalize_written_number_translations(
                        article["title_en"], title_cn
                    ),
                ),
                "abstract_cn": _canonicalize_arabic_numbers(
                    article["abstract_en"],
                    _normalize_written_number_translations(
                        article["abstract_en"], abstract_cn
                    ),
                ),
            }
            validate_translation(article, translated)
            return {
                "title_cn": translated["title_cn"].strip(),
                "abstract_cn": translated["abstract_cn"].strip(),
            }
        except (requests.RequestException, KeyError, IndexError, TranslationError) as error:
            last_error = error
            if protect_numbers and isinstance(error, TranslationError) and attempt + 1 < retries:
                # A deterministic retry repeats the same numeric drift. Give
                # the model an explicit audit correction while keeping the
                # final validation unchanged.
                payload["messages"] = _prompt(prompt_article) + [
                    {
                        "role": "user",
                        "content": (
                            "上一次输出未通过数字审计。请重新输出严格 JSON；"
                            "逐字保留所有 ⟦ADNUM_...⟧ 占位符，不得删除、翻译或改写；"
                            "除占位符恢复出的原始数字外，不得新增任何阿拉伯数字。"
                            f"审计错误：{error}"
                        ),
                    }
                ]
            status_code = (
                error.response.status_code
                if isinstance(error, requests.HTTPError) and error.response is not None
                else 0
            )
            if status_code in TERMINAL_PROVIDER_STATUS_CODES:
                raise ProviderUnavailableError(provider_name, status_code) from error
            if attempt + 1 < retries:
                retry_after = 0
                if isinstance(error, requests.HTTPError) and error.response is not None:
                    try:
                        retry_after = int(getattr(error.response, "headers", {}).get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        retry_after = 0
                if status_code == 429:
                    # DeepSeek rate limits are bursty; wait progressively
                    # longer so a throttled key can clear within one article.
                    time.sleep(max(retry_after, 10 * (attempt + 1)))
                else:
                    time.sleep(2**attempt)
    raise TranslationError(
        f"Translation failed after {retries} attempts: {last_error}"
    )


def _wait_for_google_translate_rate_limit() -> None:
    global _last_google_request_at
    now = time.monotonic()
    remaining = GOOGLE_MIN_REQUEST_INTERVAL_SECONDS - (now - _last_google_request_at)
    if remaining > 0:
        time.sleep(remaining)
    _last_google_request_at = time.monotonic()


def _google_translate_text(
    value: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 90,
) -> str:
    client = session or requests.Session()
    protected_value, number_replacements = _protect_numbers(value)
    _wait_for_google_translate_rate_limit()
    response = client.post(
        GOOGLE_TRANSLATE_ENDPOINT,
        params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t"},
        data={"q": protected_value},
        headers={"User-Agent": "Academic-Door-Journals/1.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    try:
        translated = "".join(
            str(segment[0]) for segment in body[0] if segment and segment[0]
        ).strip()
    except (IndexError, TypeError) as error:
        raise TranslationError("Google Translate returned an invalid response") from error
    if not translated:
        raise TranslationError("Google Translate returned an empty response")
    restored = _restore_numbers(translated, number_replacements)
    if re.search(r"(?:\[\[[^\]]+\]\]|⟦ADNUM_[^⟧]+⟧)", restored):
        raise TranslationError("Google Translate did not preserve numeric placeholders")
    return restored


def request_google_translation(
    article: dict[str, Any],
    *,
    session: requests.Session | None = None,
    retries: int = 3,
    timeout: int = 90,
) -> dict[str, str]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            combined = (
                f"{article['title_en']}\n{GOOGLE_SECTION_MARKER}\n"
                f"{article['abstract_en']}"
            )
            combined_cn = _google_translate_text(
                combined, session=session, timeout=timeout
            )
            parts = re.split(
                GOOGLE_SECTION_MARKER, combined_cn, maxsplit=1, flags=re.IGNORECASE
            )
            if len(parts) != 2:
                raise TranslationError("Google Translate did not preserve section marker")
            translated = {
                "title_cn": _canonicalize_arabic_numbers(
                    article["title_en"], parts[0].strip()
                ),
                "abstract_cn": _canonicalize_arabic_numbers(
                    article["abstract_en"], parts[1].strip()
                ),
            }
            translated = {
                "title_cn": _normalize_written_number_translations(
                    article["title_en"], translated["title_cn"]
                ),
                "abstract_cn": _normalize_written_number_translations(
                    article["abstract_en"], translated["abstract_cn"]
                ),
            }
            translated = {
                "title_cn": _repair_google_artifacts(
                    article["title_en"], translated["title_cn"]
                ),
                "abstract_cn": _repair_google_artifacts(
                    article["abstract_en"], translated["abstract_cn"]
                ),
            }
            validate_translation(article, translated)
            return translated
        except (requests.RequestException, TranslationError) as error:
            last_error = error
            status_code = (
                error.response.status_code
                if isinstance(error, requests.HTTPError) and error.response is not None
                else 0
            )
            if status_code in TERMINAL_PROVIDER_STATUS_CODES:
                raise ProviderUnavailableError("google-translate", status_code) from error
            if attempt + 1 < retries:
                retry_after = 0
                if isinstance(error, requests.HTTPError) and error.response is not None:
                    try:
                        retry_after = int(getattr(error.response, "headers", {}).get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        retry_after = 0
                delay = retry_after or (
                    15 * (attempt + 1) if status_code == 429 else 2**attempt
                )
                time.sleep(delay)
    raise TranslationError(
        f"Google fallback failed after {retries} attempts: {last_error}"
    )


def _write_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, cache_path)


def request_deepseek_translation(
    article: dict[str, Any],
    *,
    token: str,
    session: requests.Session | None = None,
    retries: int = 3,
) -> dict[str, str]:
    """Translate with DeepSeek while adapting to numeric-placeholder loss.

    Opaque placeholders are the safest first attempt, but some otherwise valid
    responses omit a repeated placeholder.  Retrying the same deterministic
    prompt cannot repair that.  Fall back to visible source numbers and keep
    the unchanged strict numeric validator as the final acceptance gate.
    """

    try:
        return request_translation(
            article,
            token=token,
            model=_deepseek_model(),
            endpoint=DEEPSEEK_ENDPOINT,
            session=session,
            provider_name="deepseek",
            protect_numbers=True,
            max_tokens=8192,
            retries=1,
            json_output=True,
            disable_thinking=True,
        )
    except ProviderUnavailableError:
        raise
    except TranslationError as protected_error:
        try:
            return request_translation(
                article,
                token=token,
                model=_deepseek_model(),
                endpoint=DEEPSEEK_ENDPOINT,
                session=session,
                provider_name="deepseek",
                protect_numbers=False,
                max_tokens=8192,
                retries=max(1, retries),
                json_output=True,
                disable_thinking=True,
            )
        except (ProviderUnavailableError, TranslationError) as visible_error:
            raise TranslationError(
                "DeepSeek protected-number attempt failed: "
                f"{protected_error}; visible-number retry failed: {visible_error}"
            ) from visible_error


def _translate_one_parallel(
    article: dict[str, Any],
    *,
    token: str,
    model: str,
    endpoint: str,
    google_timeout: int,
) -> dict[str, Any]:
    """Translate one article with an isolated HTTP session.

    The caller merges results into the journal cache on the main thread. This
    keeps cache writes deterministic while allowing the network-bound model
    calls to overlap in a small, explicitly configured pool.
    """

    session = requests.Session()
    translation_retries = max(
        1, min(5, int(os.environ.get("TRANSLATION_RETRIES", "5")))
    )
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    translated: dict[str, str] | None = None
    provider = "deepseek"
    primary_error: TranslationError | None = None
    if deepseek_key:
        try:
            translated = request_deepseek_translation(
                article,
                token=deepseek_key,
                session=session,
                retries=translation_retries,
            )
        except (ProviderUnavailableError, TranslationError) as error:
            primary_error = error
            print(
                f"[translate] deepseek translation failed for {article.get('doi', '')}: {error}",
                flush=True,
            )
    else:
        primary_error = TranslationError("deepseek key not configured")

    if primary_error is not None:
        provider = "github-models"
        try:
            translated = request_translation(
                article,
                token=token,
                model=model,
                endpoint=endpoint,
                session=session,
            )
        except (ProviderUnavailableError, TranslationError) as error:
            primary_error = error
        else:
            primary_error = None

    if primary_error is not None:
        provider = "google-translate"
        try:
            translated = request_google_translation(
                article, session=session, timeout=google_timeout
            )
        except (ProviderUnavailableError, TranslationError) as error:
            raise TranslationError(
                f"Primary provider failed: {primary_error}; {error}"
            ) from error

    if not translated:
        raise TranslationError("translation provider returned no content")
    return {
        "translated": translated,
        "provider": provider,
        "model": (
            model
            if provider == "github-models"
            else _deepseek_model()
            if provider == "deepseek"
            else "gtx-en-zh-CN"
        ),
        "fallback": int(provider != "deepseek"),
    }


def _translate_missing_parallel(
    issue: dict[str, Any],
    cache_path: Path,
    *,
    token: str,
    model: str,
    endpoint: str,
    max_translations: int | None,
    google_timeout: int,
    workers: int,
) -> dict[str, Any]:
    cache = (
        json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_path.exists()
        else {}
    )
    pending: list[tuple[dict[str, Any], dict[str, Any], str, bool]] = []
    invalid_cache_count = 0
    upgraded_cache_count = 0
    for article in issue["articles"]:
        if max_translations is not None and len(pending) >= max_translations:
            break
        doi = article.get("doi", "")
        comment_without_abstract = (
            article.get("article_type") == "comment"
            and not article.get("abstract_en")
        )
        official_no_abstract = has_official_no_abstract_exception(article)
        if not doi or (
            not article.get("abstract_en")
            and not comment_without_abstract
            and not official_no_abstract
        ):
            continue
        if official_no_abstract:
            # The approved exception carries a manually verified Chinese
            # title and deliberately has no abstract to send to a provider.
            continue
        existing = cache.get(doi, {})
        source_hash = _source_hash(article)
        if existing.get("title_cn") and (
            existing.get("abstract_cn")
            or comment_without_abstract
            or official_no_abstract
        ):
            try:
                validate_translation(article, existing)
                if existing.get("source_hash") and existing.get("source_hash") != source_hash:
                    raise TranslationError("Source title or abstract changed")
                if not existing.get("source_hash"):
                    existing["source_hash"] = source_hash
                    upgraded_cache_count += 1
                continue
            except TranslationError:
                invalid_cache_count += 1
        pending.append((article, existing, source_hash, comment_without_abstract))

    failures: list[dict[str, str]] = []
    translated_count = 0
    fallback_count = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="translate") as pool:
        futures = {
            pool.submit(
                _translate_one_parallel,
                article,
                token=token,
                model=model,
                endpoint=endpoint,
                google_timeout=google_timeout,
            ): (article, existing, source_hash)
            for article, existing, source_hash, _comment_without_abstract in pending
        }
        for future in as_completed(futures):
            article, existing, source_hash = futures[future]
            doi = article.get("doi", "")
            try:
                result = future.result()
            except TranslationError as error:
                failures.append(
                    {
                        "doi": doi,
                        "title_en": article.get("title_en", ""),
                        "source_hash": source_hash,
                        "provider": "deepseek/github-models/google-translate",
                        "model": model,
                        "error": str(error),
                    }
                )
                continue
            cache[doi] = {
                **existing,
                **result["translated"],
                "source_hash": source_hash,
                "translation": {
                    "provider": result["provider"],
                    "model": result["model"],
                    "prompt_version": PROMPT_VERSION,
                    "translated_at": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                },
            }
            translated_count += 1
            fallback_count += int(result["fallback"])
            _write_cache(cache_path, cache)
    _write_cache(cache_path, cache)
    return {
        "journal_id": issue["journal_id"],
        "translated": translated_count,
        "invalid_cache_entries": invalid_cache_count,
        "upgraded_cache_entries": upgraded_cache_count,
        "failed": failures,
        "provider_state": {},
        "fallback_translated": fallback_count,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "workers": workers,
    }


def translate_missing(
    issue: dict[str, Any],
    cache_path: Path,
    *,
    token: str | None = None,
    model: str | None = None,
    endpoint: str = GITHUB_MODELS_ENDPOINT,
    session: requests.Session | None = None,
    max_translations: int | None = None,
    provider_state: dict[str, str] | None = None,
    google_timeout: int = 90,
) -> dict[str, Any]:
    try:
        translation_workers = max(
            1, min(8, int(os.environ.get("TRANSLATION_WORKERS", "1")))
        )
    except ValueError:
        translation_workers = 1
    if translation_workers > 1:
        return _translate_missing_parallel(
            issue,
            cache_path,
            token=token or os.environ.get("GITHUB_TOKEN", ""),
            model=model or os.environ.get("TRANSLATION_MODEL", DEFAULT_MODEL),
            endpoint=endpoint,
            max_translations=max_translations,
            google_timeout=google_timeout,
            workers=translation_workers,
        )
    cache = (
        json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_path.exists()
        else {}
    )
    auth_token = token or os.environ.get("GITHUB_TOKEN", "")
    selected_model = model or os.environ.get("TRANSLATION_MODEL", DEFAULT_MODEL)
    try:
        translation_retries = max(
            1, min(5, int(os.environ.get("TRANSLATION_RETRIES", "5")))
        )
    except ValueError:
        translation_retries = 5
    translated_count = 0
    invalid_cache_count = 0
    upgraded_cache_count = 0
    failures: list[dict[str, str]] = []
    fallback_count = 0
    provider_availability = provider_state if provider_state is not None else {}

    for article in issue["articles"]:
        if max_translations is not None and translated_count >= max_translations:
            break
        doi = article.get("doi", "")
        comment_without_abstract = (
            article.get("article_type") == "comment"
            and not article.get("abstract_en")
        )
        official_no_abstract = has_official_no_abstract_exception(article)
        if not doi or (
            not article.get("abstract_en")
            and not comment_without_abstract
            and not official_no_abstract
        ):
            continue
        if official_no_abstract:
            continue
        existing = cache.get(doi, {})
        source_hash = _source_hash(article)
        if existing.get("title_cn") and (
            existing.get("abstract_cn")
            or comment_without_abstract
            or official_no_abstract
        ):
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
            provider = "deepseek"
            deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
            primary_error: TranslationError | None = None
            if deepseek_key and not provider_availability.get("deepseek"):
                try:
                    translated = request_deepseek_translation(
                        article,
                        token=deepseek_key,
                        session=session,
                        retries=translation_retries,
                    )
                except ProviderUnavailableError as error:
                    provider_availability["deepseek"] = str(error)
                    primary_error = error
                except TranslationError as error:
                    # A numeric-validation failure is often a transient model
                    # slip; retry DeepSeek on the next article instead of
                    # condemning the whole provider for the run.
                    print(
                        f"[translate] deepseek translation failed for {doi}: {error}",
                        flush=True,
                    )
                    primary_error = error
            else:
                primary_error = TranslationError(
                    provider_availability.get("deepseek")
                    or "deepseek key not configured"
                )
            if primary_error is not None:
                # GitHub Models fallback.
                provider = "github-models"
                if provider_availability.get("github-models"):
                    primary_error = TranslationError(
                        provider_availability["github-models"]
                    )
                else:
                    try:
                        translated = request_translation(
                            article,
                            token=auth_token,
                            model=selected_model,
                            endpoint=endpoint,
                            session=session,
                        )
                    except ProviderUnavailableError as error:
                        provider_availability["github-models"] = str(error)
                        primary_error = error
                    except TranslationError as error:
                        primary_error = error
                    else:
                        primary_error = None
            if primary_error is not None:
                if provider_availability.get("google-translate"):
                    raise TranslationError(
                        f"Primary provider failed: {primary_error}; "
                        f"{provider_availability['google-translate']}"
                    )
                try:
                    translated = request_google_translation(
                        article,
                        session=session,
                        timeout=google_timeout,
                    )
                except ProviderUnavailableError as error:
                    provider_availability["google-translate"] = str(error)
                    raise TranslationError(
                        f"Primary provider failed: {primary_error}; {error}"
                    ) from error
                except TranslationError as fallback_error:
                    raise TranslationError(
                        f"Primary provider failed: {primary_error}; {fallback_error}"
                    ) from fallback_error
                provider = "google-translate"
                fallback_count += 1
            cache[doi] = {
                **existing,
                **translated,
                "source_hash": source_hash,
                "translation": {
                    "provider": provider,
                    "model": (
                        selected_model
                        if provider == "github-models"
                        else _deepseek_model()
                        if provider == "deepseek"
                        else "gtx-en-zh-CN"
                    ),
                    "prompt_version": PROMPT_VERSION,
                    "translated_at": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                },
            }
            translated_count += 1
            _write_cache(cache_path, cache)
        except TranslationError as error:
            failures.append(
                {
                    "doi": doi,
                    "title_en": article.get("title_en", ""),
                    "source_hash": _source_hash(article),
                    "provider": provider,
                    "model": (
                        selected_model
                        if provider == "github-models"
                        else _deepseek_model()
                        if provider == "deepseek"
                        else "gtx-en-zh-CN"
                    ),
                    "error": str(error),
                }
            )

    _write_cache(cache_path, cache)
    return {
        "journal_id": issue["journal_id"],
        "translated": translated_count,
        "invalid_cache_entries": invalid_cache_count,
        "upgraded_cache_entries": upgraded_cache_count,
        "failed": failures,
        "provider_state": dict(provider_availability),
        "fallback_translated": fallback_count,
        "model": selected_model,
        "prompt_version": PROMPT_VERSION,
    }

