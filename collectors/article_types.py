"""Shared classification of non-research issue items.

Official issue pages list front matter, editorial boards, comments, replies and
special-issue introductions next to research articles. Architecture §6.5 requires
every excluded item to leave an audit record, and it requires the rule to be the
same everywhere: without a shared classifier the identical item is dropped by one
collector and counted as a research article by another. This module is the single
source of truth every collector composes on top of.
"""

from __future__ import annotations

import re

STRUCTURAL_TERMS = (
    r"front\s*matter",
    r"back\s*matter",
    r"editorial\s*board",
    r"table\s+of\s+contents",
    r"issue\s+information",
    r"^erratum(?:\s+to\b|:|\s*$)",
    r"^corrigendum(?:\s+to\b|:|\s*$)",
    r"^correction(?:\s+to\b|:|\s*$)",
    r"\bretraction\b",
    r"\baddendum\b",
    r"^acknowledg(?:e)?ments?\b",
)

DISCOURSE_TERMS = (
    r"^comment\s+on\b",
    r"^comments?\s+on\b",
    r"^comment:\s",
    r":\s*(?:a\s+)?comment\s*$",
    r"\ba\s+comment\b",
    r"^repl(?:y|ies)(?:\s+to)?\b",
    r":\s*reply\s*$",
    r"^rejoinder\b",
    r"^discussion\s+of\b",
    r"^discussion:\s",
    r"^introduction\s+to\s+the\s+special\s+issue",
    r"^special\s+issue\s+introduction",
    r"^book\s+review\b",
    r"^in\s+memoriam\b",
    r"^obituary\b",
    r"\bnobel\s+lecture\b",
    r"^presidential\s+address\b",
)


def _compile(terms: tuple[str, ...], extra: tuple[str, ...] = ()) -> re.Pattern[str]:
    return re.compile("|".join((*terms, *extra)), re.IGNORECASE)


STRUCTURAL_PATTERN = _compile(STRUCTURAL_TERMS)
DISCOURSE_PATTERN = _compile(DISCOURSE_TERMS)
NON_RESEARCH_PATTERN = STRUCTURAL_PATTERN


def build_pattern(*extra: str) -> re.Pattern[str]:
    """Return the shared exclusion rule extended with collector-specific terms.

    Only structural items (front matter, editorial boards, errata) are excluded
    here. Scholarly comments, replies and discussions stay in the roster because
    the official issue page lists them as contributions; they are labelled via
    :func:`article_type` instead. Note that ``docs/architecture.md`` §6.5 still
    reads as if comments should be excluded — that contradiction is a product
    decision, not something a collector should settle on its own.
    """

    return _compile(STRUCTURAL_TERMS, extra)


def is_non_research(title: str) -> bool:
    """True for structural items that are never scholarly contributions."""

    return bool(STRUCTURAL_PATTERN.search(title or ""))


def exclusion_reason(title: str) -> str:
    """Return a stable audit reason so excluded items stay explainable."""

    text = title or ""
    if STRUCTURAL_PATTERN.search(text):
        return "structural_item"
    if DISCOURSE_PATTERN.search(text):
        return "discourse_item"
    return ""


def type_breakdown(articles: list[dict]) -> dict[str, int]:
    """Count article types so a roster of N is always explainable."""

    counts: dict[str, int] = {}
    for item in articles:
        kind = item.get("article_type") or "research-article"
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def article_type(title: str) -> str:
    text = title or ""
    if DISCOURSE_PATTERN.search(text):
        return "comment"
    if STRUCTURAL_PATTERN.search(text):
        return "non-research"
    return "research-article"
