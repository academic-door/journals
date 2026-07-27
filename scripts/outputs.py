"""Derived public outputs: issue archive, RSS feeds and the search index.

The collectors overwrite ``issues/current.json`` on every run, so without an
archive every previous issue disappears the moment a journal publishes the next
one. These helpers are pure functions over already-validated issue payloads so
they can be tested without network access.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Iterable
from xml.sax.saxutils import escape

SITE = "https://academic-door.github.io/journals"
MAX_FEED_ITEMS = 60


def archive_path_parts(issue: dict[str, Any]) -> tuple[str, str]:
    """Return ``(journal_id, filename)`` for the immutable archive copy."""

    return issue["journal_id"], f"{issue['issue_id']}.json"


def issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    quality = issue.get("quality", {})
    return {
        "issue_id": issue["issue_id"],
        "volume": issue.get("volume", ""),
        "issue": issue.get("issue", ""),
        "issue_label": issue.get("issue_label")
        or f"Vol. {issue.get('volume', '')} · No. {issue.get('issue', '')}",
        "publication_date": issue.get("publication_date", ""),
        "retrieved_at": issue.get("retrieved_at", ""),
        "article_count": issue.get("research_article_count", 0),
        "translation_complete": quality.get("translation_complete", 0),
        "url": f"/journals/api/v1/journals/{issue['journal_id']}/issues/{issue['issue_id']}.json",
    }


def build_archive_index(
    journal_id: str,
    journal_name: str,
    archived: Iterable[dict[str, Any]],
    updated_at: str,
) -> dict[str, Any]:
    entries = sorted(
        (issue_summary(issue) for issue in archived),
        key=lambda entry: (entry["retrieved_at"], entry["issue_id"]),
        reverse=True,
    )
    return {
        "schema_version": "1.0",
        "journal_id": journal_id,
        "journal_name": journal_name,
        "updated_at": updated_at,
        "issue_count": len(entries),
        "current_url": f"/journals/api/v1/journals/{journal_id}/issues/current.json",
        "issues": entries,
    }


def _summary_text(article: dict[str, Any], limit: int = 320) -> str:
    text = (article.get("abstract_cn") or article.get("abstract_en") or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _rss_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return format_datetime(parsed)


def build_feed(
    title: str,
    description: str,
    feed_path: str,
    issues: Iterable[dict[str, Any]],
    updated_at: str,
) -> str:
    items: list[str] = []
    for issue in issues:
        journal = issue.get("journal_name", "")
        label = issue.get("issue_label") or f"Vol. {issue.get('volume', '')}"
        for article in issue.get("articles", []):
            headline = article.get("title_cn") or article.get("title_en", "")
            authors = "、".join(article.get("authors", []))
            body = _summary_text(article)
            parts = [f"{journal} {label}"]
            if authors:
                parts.append(authors)
            if body:
                parts.append(body)
            items.append(
                "    <item>\n"
                f"      <title>{escape(headline)}</title>\n"
                f"      <link>{escape(article.get('source_url', ''))}</link>\n"
                f"      <guid isPermaLink=\"false\">{escape(article.get('paper_id', ''))}</guid>\n"
                f"      <pubDate>{_rss_datetime(issue.get('retrieved_at', ''))}</pubDate>\n"
                f"      <description>{escape(' · '.join(parts))}</description>\n"
                "    </item>"
            )
    items = items[:MAX_FEED_ITEMS]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{escape(title)}</title>\n"
        f"    <link>{SITE}/</link>\n"
        f"    <description>{escape(description)}</description>\n"
        "    <language>zh-cn</language>\n"
        f"    <lastBuildDate>{_rss_datetime(updated_at)}</lastBuildDate>\n"
        f'    <atom:link href="{SITE}{feed_path}" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items)
        + "\n  </channel>\n</rss>\n"
    )


def build_search_index(
    issues: Iterable[dict[str, Any]],
    journal_meta: dict[str, dict[str, Any]],
    updated_at: str,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for issue in issues:
        meta = journal_meta.get(issue["journal_id"], {})
        for article in issue.get("articles", []):
            records.append(
                {
                    "journal_id": issue["journal_id"],
                    "short_name": meta.get("short_name", issue["journal_id"].upper()),
                    "collection": meta.get("collection", ""),
                    "issue_label": issue.get("issue_label")
                    or f"Vol. {issue.get('volume', '')}",
                    "title_en": article.get("title_en", ""),
                    "title_cn": article.get("title_cn", ""),
                    "authors": article.get("authors", []),
                    "doi": article.get("doi", ""),
                    "url": article.get("source_url", ""),
                }
            )
    return {
        "schema_version": "1.0",
        "updated_at": updated_at,
        "record_count": len(records),
        "records": records,
    }
