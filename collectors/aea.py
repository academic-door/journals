from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)

NON_RESEARCH_PATTERN = re.compile(
    r"front\s*matter|back\s*matter|erratum|correction|"
    r"\bnobel lecture\b|^comment on\b|:\s*comment$|^reply to",
    re.IGNORECASE,
)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _get(session: requests.Session, url: str, attempts: int = 4) -> requests.Response:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=45)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert error is not None
    raise error


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _fetch_detail(article_id: str, source_sequence: int) -> dict[str, Any]:
    url = f"https://www.aeaweb.org/articles?id={article_id}"
    response = _get(_session(), url)
    soup = BeautifulSoup(response.content, "html.parser")

    title = _text(soup.select_one(".title"))
    authors = [_text(node) for node in soup.select(".attribution .author")]
    authors = [name for name in authors if name]
    abstract_node = soup.select_one("section.article-information.abstract")
    abstract = _text(abstract_node)
    abstract = re.sub(r"^Abstract\s*", "", abstract, flags=re.IGNORECASE)
    doi_match = re.search(r"10\.\d{4,9}/[^\s?&#]+", article_id, flags=re.IGNORECASE)
    doi = doi_match.group(0).rstrip(".").lower() if doi_match else ""

    flags: list[str] = []
    if not doi:
        flags.append("doi_missing")
    if not authors:
        flags.append("authors_missing")
    if not abstract:
        flags.append("abstract_en_missing")
    flags.extend(["title_cn_missing", "abstract_cn_missing"])

    return {
        "paper_id": f"doi:{doi}" if doi else f"aea:{article_id}",
        "sequence": source_sequence,
        "source_sequence": source_sequence,
        "article_type": "research-article",
        "title_en": title or article_id,
        "title_cn": "",
        "authors": authors,
        "abstract_en": abstract,
        "abstract_cn": "",
        "doi": doi,
        "source_url": url,
        "publication_date": "",
        "sources": {
            "inventory": "official-issue-page",
            "title": "official-article-page",
            "authors": "official-article-page",
            "abstract": "official-article-page",
            "doi": "official-article-id",
        },
        "translation": {
            "status": "pending",
            "provider": "",
            "prompt_version": "",
            "glossary_version": "1",
        },
        "quality_flags": flags,
    }


def _fetch_detail_safe(pair: tuple[int, str]) -> dict[str, Any]:
    source_sequence, article_id = pair
    try:
        return _fetch_detail(article_id, source_sequence)
    except requests.RequestException as exc:
        doi_match = re.search(
            r"10\.\d{4,9}/[^\s?&#]+", article_id, flags=re.IGNORECASE
        )
        doi_value = doi_match.group(0).rstrip(".").lower() if doi_match else ""
        return {
            "paper_id": f"doi:{doi_value}" if doi_value else f"aea:{article_id}",
            "sequence": source_sequence,
            "source_sequence": source_sequence,
            "article_type": "unknown",
            "title_en": article_id,
            "title_cn": "",
            "authors": [],
            "abstract_en": "",
            "abstract_cn": "",
            "doi": doi_value,
            "source_url": f"https://www.aeaweb.org/articles?id={article_id}",
            "publication_date": "",
            "sources": {"inventory": "official-issue-page"},
            "translation": {
                "status": "blocked",
                "provider": "",
                "prompt_version": "",
                "glossary_version": "1",
            },
            "quality_flags": [f"detail_fetch_failed:{type(exc).__name__}"],
        }


def fetch_current_issue(current_issue_url: str) -> dict[str, Any]:
    session = _session()
    response = _get(session, current_issue_url)
    soup = BeautifulSoup(response.content, "html.parser")

    header = _text(soup.select_one("h1.issue"))
    match = re.search(r"Vol\.\s*(\d+),\s*No\.\s*(\d+)", header, flags=re.IGNORECASE)
    volume, issue = match.groups() if match else ("", "")

    ordered_ids: list[str] = []
    for article in soup.select("article.journal-article"):
        classes = set(article.get("class", []))
        article_id = article.get("id", "").strip()
        if not article_id or "symposia-title" in classes:
            continue
        ordered_ids.append(article_id)

    # executor.map preserves input order, unlike appending futures as they finish.
    pairs = list(enumerate(ordered_ids, start=1))
    with ThreadPoolExecutor(max_workers=3) as pool:
        all_items = list(pool.map(_fetch_detail_safe, pairs))

    excluded_items = [
        {
            "source_sequence": article["source_sequence"],
            "title_en": article["title_en"],
            "reason": "non-research-title",
        }
        for article in all_items
        if NON_RESEARCH_PATTERN.search(article["title_en"])
    ]
    articles = [
        article
        for article in all_items
        if not NON_RESEARCH_PATTERN.search(article["title_en"])
    ]
    for sequence, article in enumerate(articles, start=1):
        article["sequence"] = sequence

    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    translation_complete = sum(
        bool(article["title_cn"] and article["abstract_cn"]) for article in articles
    )
    detail_failure_count = sum(
        any(flag.startswith("detail_fetch_failed:") for flag in article["quality_flags"])
        for article in all_items
    )
    flags: list[str] = []
    if not match:
        flags.append("volume_issue_unparsed")
    if len(all_items) != len(ordered_ids):
        flags.append("official_inventory_mismatch")
    if detail_failure_count:
        flags.append("article_detail_fetch_incomplete")
    if translation_complete != len(articles):
        flags.append("translation_incomplete")
    if duplicate_count:
        flags.append("duplicate_doi")

    status = "ready" if not flags else "incomplete"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    issue_id = f"aer-{volume or 'unknown'}-{issue or 'current'}"

    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": "aer",
        "journal_name": "American Economic Review",
        "volume": volume,
        "issue": issue,
        "publication_date": "",
        "source_url": current_issue_url,
        "retrieved_at": now,
        "expected_article_count": len(articles),
        "research_article_count": len(articles),
        "status": status,
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": len(all_items) == len(ordered_ids),
            "order_preserved": True,
            "official_item_count": len(all_items),
            "excluded_item_count": len(excluded_items),
            "excluded_items": excluded_items,
            "detail_failure_count": detail_failure_count,
            "doi_complete": sum(bool(article["doi"]) for article in articles),
            "authors_complete": sum(bool(article["authors"]) for article in articles),
            "abstract_en_complete": sum(
                bool(article["abstract_en"]) for article in articles
            ),
            "translation_complete": translation_complete,
            "duplicate_count": duplicate_count,
            "flags": flags,
        },
    }
