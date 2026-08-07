from __future__ import annotations

import re
from collections import Counter
from typing import Any


ARTICLE_TYPES = {
    "research-article",
    "comment",
    "short-communication",
    "correction",
    "editorial",
    "front-matter",
    "other",
}
PUBLISHABLE_TYPES = {"research-article", "comment", "short-communication"}
CORRECTION_PATTERN = re.compile(
    r"^\s*(?:corrigendum|erratum|correction|addendum)(?:\s+to\b|:|\s*$)|"
    r"\bretraction\s+(?:notice|note)\b|"
    r"^retraction(?:\s+of\b|:|\s*$)|^expression\s+of\s+concern",
    re.IGNORECASE,
)
EDITORIAL_PATTERN = re.compile(
    r"editorial\s+board|editors?[’'＊]?s?\s+notes?|reports?|recent\s+referees|"
    r"acknowledg(?:e)?ments?\s+of\s+referees|annual\s+report|"
    r"report\s+of\s+the\s+est\b|report\s+of\s+the\s+editor.+for\s+the\s+year|annual\s+membership\s+meeting|"
    r"prizes?\s+for\s+\d{4}|fischer\s+black\s+prize|brattle\s+group|"
    r"dimensional\s+fund\s+advisors|excellence\s+in\s+refereeing\s+award|"
    r"^\s*editorial(?:\s|:|$)|"
    r"^\s*(?:an\s+)?issue\s+dedicated\s+to\b|"
    r"^\s*special\s+issue\b|themed\s+issue\b|"
    r"^introductory\s+essay\b|^\s*presidents?\s*$|"
    r"\bintroduction(?:\s+to\b|\s*:|\s*$)",
    re.IGNORECASE,
)
FRONT_MATTER_PATTERN = re.compile(
    r"front\s*matter|back\s*matter|table\s+of\s+contents|"
    r"issue\s+information|submission\s+of\s+manuscripts|"
    r"fellows?(?:\s+of\b|:|\s*$)|^award\s+winning\s+theses?\s*$",
    re.IGNORECASE,
)
# Bare person-name titles ("Titus Awokuse", "Gerald 'Jerry' Shively") are
# memorials/fellows announcements in many journals; when such an item also has
# no abstract, it is front matter, not research.
PERSON_NAME_PATTERN = re.compile(
    r"^[A-Z][A-Za-z.'\-]*(?:\s+['\"“”‘’（(]?[A-Z][A-Za-z.'\-()”]*){0,4}$"
)


def is_person_name_title(title: str) -> bool:
    return bool(PERSON_NAME_PATTERN.fullmatch(str(title or "").strip()))


SHORT_PATTERN = re.compile(
    r"short\s+communication|short\s+paper|research\s+note|brief\s+report",
    re.IGNORECASE,
)
COMMENT_PATTERN = re.compile(
    r"\ba\s+comment\b|^\s*comments?(?:\s+on)?\b|"
    r"^\s*reply(?:\s+to)?\b|:\s*(?:a\s+)?comments?\s*$|"
    r":\s*reply\s*$|\bdiscussion\b",
    re.IGNORECASE,
)


def canonical_issue_label(
    volume: object,
    issue: object,
    existing: object = "",
) -> str:
    """Return a reader-facing label without leaking publisher URL tokens."""

    volume_text = str(volume or "").strip()
    issue_text = str(issue or "").strip()
    existing_text = str(existing or "").strip()
    if not volume_text:
        return existing_text
    base = f"Vol. {volume_text}"
    if not issue_text or issue_text.casefold() == "c":
        return base
    if issue_text.casefold() in {"a", "b"}:
        return f"{base} · Part {issue_text.upper()}"
    if re.fullmatch(r"\d+(?:\s*[-–]\s*\d+)?", issue_text):
        return f"{base} · No. {issue_text}"
    return existing_text or f"{base} · {issue_text}"


def canonical_article_type(
    title: str,
    article_type: str = "",
    *,
    raw_type: str = "",
) -> str:
    """Normalize publisher-specific labels into the shared journal taxonomy."""

    current = str(article_type or "").strip().casefold().replace("_", "-")
    combined = " ".join(value for value in (raw_type, title) if value)
    if CORRECTION_PATTERN.search(combined):
        return "correction"
    if EDITORIAL_PATTERN.search(combined):
        return "editorial"
    if FRONT_MATTER_PATTERN.search(combined):
        return "front-matter"
    if SHORT_PATTERN.search(combined):
        return "short-communication"
    if COMMENT_PATTERN.search(combined):
        return "comment"
    aliases = {
        "article": "research-article",
        "journal-article": "research-article",
        "original-article": "research-article",
        "regular-paper": "research-article",
        "research-article": "research-article",
        "commentary": "comment",
        "reply": "comment",
        "short-communication": "short-communication",
        "short-communications": "short-communication",
        "erratum": "correction",
        "corrigendum": "correction",
        "correction": "correction",
        "editorial": "editorial",
        "front-matter": "front-matter",
    }
    return aliases.get(current, current if current in ARTICLE_TYPES else "research-article")


def is_publishable_type(article_type: str) -> bool:
    return canonical_article_type("", article_type) in PUBLISHABLE_TYPES


def requires_abstract(article_type: str) -> bool:
    return canonical_article_type("", article_type) in {
        "research-article",
        "short-communication",
    }


def abstract_is_complete(article: dict[str, Any]) -> bool:
    return bool(str(article.get("abstract_en", "")).strip()) or not requires_abstract(
        str(article.get("article_type", ""))
    )


def translation_is_complete(article: dict[str, Any]) -> bool:
    if not str(article.get("title_cn", "")).strip():
        return False
    abstract_en = str(article.get("abstract_en", "")).strip()
    abstract_cn = str(article.get("abstract_cn", "")).strip()
    if abstract_en:
        return bool(abstract_cn)
    return not requires_abstract(str(article.get("article_type", "")))


def exclusion_reason(article_type: str) -> str:
    return {
        "correction": "correction_notice",
        "editorial": "editorial_material",
        "front-matter": "front_matter",
    }.get(canonical_article_type("", article_type), "non_publishable_item")


def normalize_issue_taxonomy(
    issue: dict[str, Any],
    *,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Apply canonical types, trustworthy progress counts, and issue totals."""

    type_overrides = {
        str(key).strip().lower(): str(value)
        for key, value in (overrides or {}).items()
        if str(key).strip()
    }
    articles = issue.get("articles", [])
    for article in articles:
        doi = str(article.get("doi", "")).strip().lower()
        forced = type_overrides.get(doi, "")
        atype = canonical_article_type(
            str(article.get("title_en", "")),
            forced or str(article.get("article_type", "")),
        )
        # A bare person-name title is a memorial or fellows announcement
        # (front matter), not a research article: either it has no English
        # abstract (the Crossref record is an empty announcement) or it has no
        # authors (the person is the subject of the notice, not the author).
        if (
            atype == "research-article"
            and is_person_name_title(str(article.get("title_en", "")))
            and (
                not str(article.get("abstract_en", "")).strip()
                or not article.get("authors")
            )
        ):
            atype = "front-matter"
        article["article_type"] = atype

    quality = issue.setdefault("quality", {})
    excluded = quality.setdefault("excluded_items", [])
    # Reclassification is authoritative: items that normalized to a
    # non-publishable type (correction/editorial/front-matter/other) must not
    # count toward the publishable roster or block translation completion.
    kept: list[dict[str, Any]] = []
    for article in articles:
        if is_publishable_type(str(article.get("article_type", ""))):
            kept.append(article)
        else:
            excluded.append(
                {
                    "title_en": article.get("title_en", ""),
                    "doi": article.get("doi", ""),
                    "article_type": article.get("article_type", "other"),
                    "reason": exclusion_reason(
                        str(article.get("article_type", "other"))
                    ),
                }
            )
    articles[:] = kept
    for index, article in enumerate(articles, start=1):
        article["sequence"] = index
        article.setdefault("source_sequence", 0)
        article["source_sequence"] = article["source_sequence"] or index

    for item in excluded:
        item_type = canonical_article_type(
            str(item.get("title_en", "")),
            str(item.get("article_type", "")),
        )
        item["article_type"] = item_type
        if not item.get("reason") or item.get("reason") == "non_research_title":
            item["reason"] = exclusion_reason(item_type)

    included_counts = Counter(
        str(article.get("article_type", "research-article")) for article in articles
    )
    excluded_counts = Counter(
        str(item.get("article_type", "other")) for item in excluded
    )
    observed_items = len(articles) + len(excluded)
    stated_official = int(quality.get("official_item_count", 0) or 0)
    official_items = max(observed_items, stated_official)
    counts = {
        "official_items": official_items,
        "observed_items": observed_items,
        "publishable_items": len(articles),
        "research_articles": included_counts["research-article"],
        "comments": included_counts["comment"],
        "short_communications": included_counts["short-communication"],
        "corrections": excluded_counts["correction"],
        "editorial_material": excluded_counts["editorial"],
        "front_matter": excluded_counts["front-matter"],
        "other_excluded": len(excluded)
        - excluded_counts["correction"]
        - excluded_counts["editorial"]
        - excluded_counts["front-matter"],
    }
    counts["catalog_content_items"] = (
        counts["publishable_items"] + counts["corrections"]
    )
    issue["content_counts"] = counts
    quality["official_item_count"] = official_items
    quality["publishable_item_count"] = len(articles)
    quality["excluded_item_count"] = len(excluded)
    quality["content_counts"] = counts
    quality["abstract_en_complete"] = sum(abstract_is_complete(item) for item in articles)
    quality["translation_complete"] = sum(
        translation_is_complete(item) for item in articles
    )
    quality["doi_complete"] = sum(bool(item.get("doi")) for item in articles)
    quality["authors_complete"] = sum(bool(item.get("authors")) for item in articles)

    flags = [
        str(flag)
        for flag in quality.get("flags", [])
        if flag not in {"abstract_en_incomplete", "translation_incomplete"}
    ]
    if quality["abstract_en_complete"] != len(articles):
        flags.append("abstract_en_incomplete")
    if quality["translation_complete"] != len(articles):
        flags.append("translation_incomplete")
    quality["flags"] = list(dict.fromkeys(flags))
    issue["expected_article_count"] = len(articles)
    issue["research_article_count"] = len(articles)
    return issue
