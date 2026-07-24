from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup


CHICAGO_ORIGIN = "https://www.journals.uchicago.edu"
USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
DEFAULT_TIMEOUT = 45
DEFAULT_ATTEMPTS = 4
DEFAULT_BACKOFF = 1.5

DOI_PATTERN = re.compile(
    r"10\.\d{4,9}/[^\s?&#\"'<>]+",
    flags=re.IGNORECASE,
)
VOLUME_ISSUE_PATTERN = re.compile(
    r"(?:Vol(?:ume)?\.?)\s*(\d+)\s*,?\s*"
    r"(?:No\.?|Number|Issue)\s*(\d+)",
    flags=re.IGNORECASE,
)
MONTH_YEAR_PATTERN = re.compile(
    r"\b("
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December"
    r")\s+(\d{4})\b",
    flags=re.IGNORECASE,
)

TOC_CONTAINER_SELECTORS = (
    ".issue-item",
    ".toc-item",
    "[class*='toc-item']",
    "article.issue-item",
)
TOC_ROOT_SELECTORS = (
    ".table-of-content",
    ".table-of-contents",
    ".toc-container",
    ".issue-items-container",
    "[class*='table-of-content']",
    "main",
)
ARTICLE_ABSTRACT_SELECTORS = (
    ".abstractSection.abstractInFull",
    "section.abstract",
    ".article__abstract",
    ".article-abstract",
    "#abstract",
    "[class*='abstractInFull']",
)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.8",
        }
    )
    return session


def _get(
    session: requests.Session,
    url: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    timeout: int = DEFAULT_TIMEOUT,
    backoff: float = DEFAULT_BACKOFF,
    sleeper: Callable[[float], None] = time.sleep,
) -> requests.Response:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt + 1 < attempts:
                sleeper(backoff * (attempt + 1))

    assert error is not None
    raise error


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _meta_content(
    soup: BeautifulSoup,
    *names: str,
    all_values: bool = False,
) -> str | list[str]:
    lowered = {name.casefold() for name in names}
    values: list[str] = []
    for node in soup.select("meta[content]"):
        key = (node.get("name") or node.get("property") or "").strip().casefold()
        value = node.get("content", "").strip()
        if key in lowered and value:
            values.append(value)
    if all_values:
        return values
    return values[0] if values else ""


def _extract_doi(value: str) -> str:
    decoded = unquote(value or "")
    match = DOI_PATTERN.search(decoded)
    if not match:
        return ""
    return match.group(0).rstrip(".,;:)]}").lower()


def _canonical_article_url(value: str, base_url: str = CHICAGO_ORIGIN) -> str:
    doi = _extract_doi(value)
    if doi:
        return f"{CHICAGO_ORIGIN}/doi/{doi}"
    return urljoin(base_url, value)


def _normalize_publication_date(value: str) -> str:
    value = " ".join((value or "").split())
    if not value:
        return ""
    for source_format, target_format in (
        ("%B %Y", "%Y-%m"),
        ("%b %Y", "%Y-%m"),
        ("%Y/%m/%d", "%Y-%m-%d"),
        ("%Y-%m-%d", "%Y-%m-%d"),
        ("%m/%d/%Y", "%Y-%m-%d"),
    ):
        try:
            return datetime.strptime(value, source_format).strftime(target_format)
        except ValueError:
            continue
    return value


def classify_toc_item(title: str, section: str = "") -> tuple[str, str]:
    normalized_title = " ".join((title or "").split())
    normalized_section = " ".join((section or "").split())
    title_key = normalized_title.casefold()
    section_key = normalized_section.casefold()

    if re.search(r"\bfront\s+matter\b", title_key) or section_key == "front matter":
        return "front-matter", "front-matter"
    if re.search(r"\bback\s+matter\b", title_key) or section_key == "back matter":
        return "back-matter", "back-matter"
    if re.search(r"\bturnaround\s+times?\b", title_key):
        return "turnaround-times", "publisher-administration"
    if re.search(r"\brecent\s+referees?\b", title_key):
        return "recent-referees", "publisher-administration"
    if (
        re.search(r"^(?:a\s+)?comment(?:ary)?\b", title_key)
        or re.search(r":\s*(?:a\s+)?comment(?:ary)?\s*$", title_key)
        or re.search(r"^reply\b|:\s*reply\s*$", title_key)
        or section_key
        in {
            "comment",
            "comments",
            "comment and reply",
            "comments and replies",
        }
    ):
        return "comment", "comment-or-reply"
    if re.search(r"^(?:erratum|correction|corrigendum)\b", title_key):
        return "correction", "correction"
    return "research-article", ""


def _section_for(container: Any) -> str:
    for heading in container.find_all_previous(["h2", "h3", "h4", "h5"]):
        if heading.find("a", href=lambda href: bool(href and _extract_doi(href))):
            continue
        value = _text(heading)
        if value:
            return value
    return ""


def _authors_from_container(container: Any) -> list[str]:
    selectors = (
        ".issue-item__loa .author",
        ".issue-item__loa [class*='Author']",
        ".authors .author",
        "[class*='ContribAuthor']",
        "[class*='author-name']",
    )
    authors: list[str] = []
    for selector in selectors:
        for node in container.select(selector):
            value = _text(node).strip(" ,")
            if value and value.casefold() not in {"and", "&"} and value not in authors:
                authors.append(value)
        if authors:
            break
    return authors


def _item_from_container(
    container: Any,
    *,
    source_sequence: int,
    base_url: str,
) -> dict[str, Any] | None:
    doi_link = None
    for link in container.select("a[href]"):
        href = link.get("href", "")
        if _extract_doi(href):
            doi_link = link
            break
    if doi_link is None:
        return None

    href = doi_link.get("href", "")
    title_node = container.select_one(
        ".issue-item__title, .toc-item__title, "
        "[class*='issue-item'][class*='title'], h2, h3, h4, h5"
    )
    title = _text(title_node) or _text(doi_link)
    title = re.sub(
        r"^(?:Open Access|Free|No Access)\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    section = _section_for(container)
    article_type, exclusion_reason = classify_toc_item(title, section)
    return {
        "source_sequence": source_sequence,
        "title_en": title or _extract_doi(href),
        "authors": _authors_from_container(container),
        "doi": _extract_doi(href),
        "source_url": _canonical_article_url(href, base_url),
        "section": section,
        "article_type": article_type,
        "exclusion_reason": exclusion_reason,
    }


def _parse_issue_page(html: bytes | str, source_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    flags: list[str] = []

    header_candidates = [
        _meta_content(soup, "citation_title", "og:title"),
        *[
            _text(node)
            for node in soup.select(
                ".issue-header, .issue-info, .journalMeta, "
                ".toc-heading, h1, h2, h3, title"
            )
        ],
    ]
    volume = str(_meta_content(soup, "citation_volume"))
    issue = str(_meta_content(soup, "citation_issue"))
    if not (volume and issue):
        for candidate in header_candidates:
            match = VOLUME_ISSUE_PATTERN.search(str(candidate))
            if match:
                volume, issue = match.groups()
                break

    publication_date = str(
        _meta_content(
            soup,
            "citation_publication_date",
            "citation_date",
            "dc.date",
            "prism.publicationdate",
        )
    )
    if not publication_date:
        date_nodes = soup.select(
            ".issue-date, .issue-info__date, "
            "[class*='issue'][class*='date'], .journalMeta"
        )
        for candidate in [*map(_text, date_nodes), *map(str, header_candidates)]:
            match = MONTH_YEAR_PATTERN.search(candidate)
            if match:
                publication_date = match.group(0)
                break
    publication_date = _normalize_publication_date(publication_date)

    containers: list[Any] = []
    seen_nodes: set[int] = set()
    for selector in TOC_CONTAINER_SELECTORS:
        for node in soup.select(selector):
            marker = id(node)
            if marker not in seen_nodes:
                seen_nodes.add(marker)
                containers.append(node)

    used_anchor_fallback = False
    if not containers:
        used_anchor_fallback = True
        root = None
        for selector in TOC_ROOT_SELECTORS:
            root = soup.select_one(selector)
            if root is not None:
                break
        root = root or soup
        for link in root.select("a[href]"):
            if not _extract_doi(link.get("href", "")):
                continue
            container = link.find_parent(["article", "li", "div"]) or link.parent
            marker = id(container)
            if marker not in seen_nodes:
                seen_nodes.add(marker)
                containers.append(container)

    items: list[dict[str, Any]] = []
    seen_dois: set[str] = set()
    for container in containers:
        item = _item_from_container(
            container,
            source_sequence=len(items) + 1,
            base_url=source_url,
        )
        if item is None or item["doi"] in seen_dois:
            continue
        seen_dois.add(item["doi"])
        item["source_sequence"] = len(items) + 1
        items.append(item)

    if used_anchor_fallback:
        flags.append("toc_container_fallback")
    if not items:
        flags.append("official_inventory_empty")

    return {
        "volume": volume,
        "issue": issue,
        "publication_date": publication_date,
        "items": items,
        "flags": flags,
    }


def _json_ld_people(soup: BeautifulSoup) -> list[str]:
    import json

    people: list[str] = []
    for node in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(node.string or "")
        except (TypeError, ValueError):
            continue
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            authors = entry.get("author", [])
            authors = authors if isinstance(authors, list) else [authors]
            for author in authors:
                if isinstance(author, dict):
                    name = str(author.get("name", "")).strip()
                else:
                    name = str(author).strip()
                if name and name not in people:
                    people.append(name)
    return people


def _parse_article_page(
    html: bytes | str,
    seed: dict[str, Any],
    *,
    expected_volume: str = "",
    expected_issue: str = "",
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    detail_title = str(
        _meta_content(
            soup,
            "citation_title",
            "dc.title",
            "og:title",
        )
    )
    if not detail_title:
        detail_title = _text(
            soup.select_one(
                "h1.article-title, h1.citation__title, "
                ".article__title h1, main h1, h1"
            )
        )
    title = detail_title or seed["title_en"]

    detail_authors = [
        value.strip()
        for value in _meta_content(
            soup,
            "citation_author",
            "dc.creator",
            all_values=True,
        )
        if value.strip()
    ]
    if not detail_authors:
        detail_authors = _json_ld_people(soup)
    if not detail_authors:
        detail_authors = _authors_from_container(soup)
    authors = detail_authors or list(seed.get("authors", []))

    abstract = ""
    for selector in ARTICLE_ABSTRACT_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            abstract = _text(node)
            if abstract:
                break
    if not abstract:
        abstract = str(
            _meta_content(
                soup,
                "citation_abstract",
                "dc.description",
            )
        )
    abstract = re.sub(r"^Abstract\s*", "", abstract, flags=re.IGNORECASE)

    detail_doi = _extract_doi(
        str(
            _meta_content(
                soup,
                "citation_doi",
                "dc.identifier",
                "prism.doi",
            )
        )
    )
    doi = detail_doi or seed["doi"] or _extract_doi(seed["source_url"])

    article_volume = str(_meta_content(soup, "citation_volume"))
    article_issue = str(_meta_content(soup, "citation_issue"))
    publication_date = _normalize_publication_date(
        str(
            _meta_content(
                soup,
                "citation_publication_date",
                "citation_date",
                "dc.date",
            )
        )
    )

    flags: list[str] = []
    if not doi:
        flags.append("doi_missing")
    if not authors:
        flags.append("authors_missing")
    if not abstract:
        flags.append("abstract_en_missing")
    if expected_volume and article_volume and article_volume != expected_volume:
        flags.append("volume_mismatch")
    if expected_issue and article_issue and article_issue != expected_issue:
        flags.append("issue_mismatch")
    flags.extend(["title_cn_missing", "abstract_cn_missing"])

    return {
        "paper_id": (
            f"doi:{doi}"
            if doi
            else f"chicago:{seed['source_sequence']}:{seed['title_en']}"
        ),
        "sequence": seed["source_sequence"],
        "source_sequence": seed["source_sequence"],
        "article_type": "research-article",
        "title_en": title,
        "title_cn": "",
        "authors": authors,
        "abstract_en": abstract,
        "abstract_cn": "",
        "doi": doi,
        "source_url": _canonical_article_url(seed["source_url"]),
        "publication_date": publication_date,
        "sources": {
            "inventory": "official-issue-page",
            "title": (
                "official-article-page" if detail_title else "official-issue-page"
            ),
            "authors": (
                "official-article-page" if detail_authors else "official-issue-page"
            ),
            "abstract": "official-article-page",
            "doi": (
                "official-article-page" if detail_doi else "official-issue-page"
            ),
            "volume": "official-issue-page",
            "issue": "official-issue-page",
        },
        "translation": {
            "status": "pending",
            "provider": "",
            "prompt_version": "",
            "glossary_version": "1",
        },
        "quality_flags": flags,
    }


def _fetch_detail(
    seed: dict[str, Any],
    *,
    expected_volume: str,
    expected_issue: str,
    timeout: int,
) -> dict[str, Any]:
    response = _get(_session(), seed["source_url"], timeout=timeout)
    return _parse_article_page(
        response.content,
        seed,
        expected_volume=expected_volume,
        expected_issue=expected_issue,
    )


def _fetch_detail_safe(
    seed: dict[str, Any],
    *,
    expected_volume: str,
    expected_issue: str,
    timeout: int,
) -> dict[str, Any]:
    try:
        return _fetch_detail(
            seed,
            expected_volume=expected_volume,
            expected_issue=expected_issue,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        doi = seed["doi"]
        return {
            "paper_id": (
                f"doi:{doi}"
                if doi
                else f"chicago:{seed['source_sequence']}:{seed['title_en']}"
            ),
            "sequence": seed["source_sequence"],
            "source_sequence": seed["source_sequence"],
            "article_type": "unknown",
            "title_en": seed["title_en"],
            "title_cn": "",
            "authors": list(seed.get("authors", [])),
            "abstract_en": "",
            "abstract_cn": "",
            "doi": doi,
            "source_url": seed["source_url"],
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


def fetch_current_issue(
    current_issue_url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_workers: int = 3,
) -> dict[str, Any]:
    response = _get(_session(), current_issue_url, timeout=timeout)
    parsed = _parse_issue_page(response.content, current_issue_url)
    volume = parsed["volume"]
    issue = parsed["issue"]
    official_items = parsed["items"]

    excluded_items = [
        {
            "source_sequence": item["source_sequence"],
            "title_en": item["title_en"],
            "article_type": item["article_type"],
            "reason": item["exclusion_reason"],
            "source_url": item["source_url"],
            "doi": item["doi"],
            "section": item["section"],
        }
        for item in official_items
        if item["article_type"] != "research-article"
    ]
    research_seeds = [
        item for item in official_items if item["article_type"] == "research-article"
    ]

    def fetch(seed: dict[str, Any]) -> dict[str, Any]:
        return _fetch_detail_safe(
            seed,
            expected_volume=volume,
            expected_issue=issue,
            timeout=timeout,
        )

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        articles = list(pool.map(fetch, research_seeds))

    source_order = [article["source_sequence"] for article in articles]
    order_preserved = source_order == sorted(source_order)
    for sequence, article in enumerate(articles, start=1):
        article["sequence"] = sequence

    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    detail_failure_count = sum(
        any(flag.startswith("detail_fetch_failed:") for flag in article["quality_flags"])
        for article in articles
    )
    translation_complete = sum(
        bool(article["title_cn"] and article["abstract_cn"]) for article in articles
    )
    roster_match = len(articles) == len(research_seeds)

    flags = list(parsed["flags"])
    if not (volume and issue):
        flags.append("volume_issue_unparsed")
    if not roster_match:
        flags.append("official_inventory_mismatch")
    if not order_preserved:
        flags.append("order_not_preserved")
    if detail_failure_count:
        flags.append("article_detail_fetch_incomplete")
    if translation_complete != len(articles):
        flags.append("translation_incomplete")
    if duplicate_count:
        flags.append("duplicate_doi")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "schema_version": "1.0",
        "issue_id": f"jpe-{volume or 'unknown'}-{issue or 'current'}",
        "journal_id": "jpe",
        "journal_name": "Journal of Political Economy",
        "volume": volume,
        "issue": issue,
        "publication_date": parsed["publication_date"],
        "source_url": current_issue_url,
        "retrieved_at": now,
        "expected_article_count": len(research_seeds),
        "research_article_count": len(articles),
        "status": "ready" if not flags else "incomplete",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": roster_match,
            "order_preserved": order_preserved,
            "official_item_count": len(official_items),
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
