from __future__ import annotations

import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
REQUEST_TIMEOUT = (10, 45)
MAX_ATTEMPTS = 4
DETAIL_WORKERS = 3

JOURNALS: dict[str, dict[str, str]] = {
    "qje": {
        "journal_id": "qje",
        "slug": "qje",
        "journal_name": "The Quarterly Journal of Economics",
        "current_issue_url": "https://academic.oup.com/qje/issue",
    },
    "restud": {
        # Academic Door's current config uses "res"; Oxford uses "restud" in URLs.
        "journal_id": "res",
        "slug": "restud",
        "journal_name": "The Review of Economic Studies",
        "current_issue_url": "https://academic.oup.com/restud/issue",
    },
}

ALIASES = {
    "qje": "qje",
    "res": "restud",
    "restud": "restud",
}

TRANSIENT_STATUS_CODES = {403, 408, 425, 429, 500, 502, 503, 504}

NON_RESEARCH_PATTERN = re.compile(
    r"front\s*matter|back\s*matter|table\s+of\s+contents|editorial\s+board|"
    r"erratum|corrigendum|correction|retraction|book\s+review|"
    r"\bnobel lecture\b|^comment on\b|:\s*comment$|^reply to",
    re.IGNORECASE,
)

NON_RESEARCH_TYPE_PATTERN = re.compile(
    r"editorial|correction|erratum|corrigendum|retraction|book\s*review|"
    r"front\s*matter|back\s*matter|table\s+of\s+contents",
    re.IGNORECASE,
)

ISSUE_PATTERN = re.compile(
    r"Volume\s+(\d+)\s*,?\s*Issue\s+([A-Za-z0-9.-]+)",
    re.IGNORECASE,
)

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"'<>?&#]+", re.IGNORECASE)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        }
    )
    return session


def _get(
    session: requests.Session,
    url: str,
    attempts: int = MAX_ATTEMPTS,
) -> requests.Response:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            if (
                response.status_code in TRANSIENT_STATUS_CODES
                and attempt + 1 < attempts
            ):
                retry_after = response.headers.get("Retry-After", "").strip()
                delay = (
                    min(float(retry_after), 15.0)
                    if retry_after.replace(".", "", 1).isdigit()
                    else 1.5 * (attempt + 1)
                )
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    if error is not None:
        raise error
    raise requests.RequestException(f"Failed to fetch {url}")


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split()).strip(" ,")
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            result.append(normalized)
    return result


def _normalize_doi(value: str) -> str:
    match = DOI_PATTERN.search(value or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,);]").lower()


def _meta_values(soup: BeautifulSoup, *names: str) -> list[str]:
    wanted = {name.casefold() for name in names}
    values: list[str] = []
    for node in soup.find_all("meta"):
        name = str(node.get("name") or node.get("property") or "").casefold()
        if name in wanted:
            content = str(node.get("content") or "").strip()
            if content:
                values.append(content)
    return values


def _json_ld_articles(soup: BeautifulSoup) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        type_value = value.get("@type", "")
        types = type_value if isinstance(type_value, list) else [type_value]
        if any(
            str(item).casefold() in {"scholarlyarticle", "article"}
            for item in types
        ):
            articles.append(value)
        graph = value.get("@graph")
        if graph is not None:
            visit(graph)

    for node in soup.select("script[type='application/ld+json']"):
        try:
            visit(json.loads(node.string or node.get_text()))
        except (json.JSONDecodeError, TypeError):
            continue
    return articles


def _official_issue_url(url: str, slug: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "academic.oup.com"
        and (path == f"/{slug}/issue" or path.startswith(f"/{slug}/issue/"))
    )


def _official_article_url(url: str, slug: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "academic.oup.com"
        and parsed.path.startswith(f"/{slug}/article/")
    )


def _resolve_journal(
    journal_or_url: str,
    current_issue_url: str | None,
) -> tuple[dict[str, str], str]:
    value = journal_or_url.strip()
    if value.startswith("http://") or value.startswith("https://"):
        if current_issue_url is not None:
            raise ValueError(
                "Pass either a journal id plus current_issue_url, or one issue URL"
            )
        parsed = urlparse(value)
        segments = [segment for segment in parsed.path.split("/") if segment]
        key = ALIASES.get(segments[0].casefold() if segments else "")
        if key is None:
            raise ValueError(f"Unsupported Oxford Academic journal URL: {value}")
        issue_url = value
    else:
        key = ALIASES.get(value.casefold())
        if key is None:
            raise ValueError(f"Unsupported Oxford Academic journal: {value}")
        issue_url = current_issue_url or JOURNALS[key]["current_issue_url"]

    spec = JOURNALS[key]
    if not _official_issue_url(issue_url, spec["slug"]):
        raise ValueError(
            "Oxford collector only accepts the journal's official "
            f"academic.oup.com/{spec['slug']}/issue page"
        )
    return spec, issue_url


def _extract_issue_identity(soup: BeautifulSoup) -> tuple[str, str, str]:
    candidates: list[str] = []
    for selector in (
        ".issue-info h1",
        ".issue-header h1",
        ".issue-info__title",
        "main h1",
        "h1",
    ):
        candidates.extend(_text(node) for node in soup.select(selector))

    selected = ""
    match: re.Match[str] | None = None
    for candidate in candidates:
        candidate_match = ISSUE_PATTERN.search(candidate)
        if candidate_match:
            selected = candidate
            match = candidate_match
            break

    if match is None:
        page_text = _text(soup)
        matches = list(ISSUE_PATTERN.finditer(page_text))
        if matches:
            counts = Counter(
                (item.group(1), item.group(2)) for item in matches
            )
            volume_issue, _ = counts.most_common(1)[0]
            match = next(
                item
                for item in matches
                if (item.group(1), item.group(2)) == volume_issue
            )
            start = match.start()
            selected = page_text[start : start + 120]

    if match is None:
        return "", "", ""

    volume, issue = match.groups()
    date_match = re.search(
        rf"Issue\s+{re.escape(issue)}\s*,?\s*"
        r"([A-Za-z]+\s+\d{4}|\d{4})",
        selected,
        flags=re.IGNORECASE,
    )
    publication_date = date_match.group(1) if date_match else ""
    return volume, issue, publication_date


def _first_article_link(card: Any, issue_url: str, slug: str) -> Any:
    for selector in (
        ".al-title-list a[href]",
        "h5 a[href]",
        "h4 a[href]",
        "h3 a[href]",
        "a[href*='/article/']",
    ):
        for link in card.select(selector):
            url = urljoin(issue_url, str(link.get("href") or ""))
            if _official_article_url(url, slug):
                return link
    return None


def _exclusion_reason(title: str, article_type: str) -> str:
    if NON_RESEARCH_TYPE_PATTERN.search(article_type):
        return f"non-research-type:{article_type.casefold().replace(' ', '-')}"
    if NON_RESEARCH_PATTERN.search(title):
        return "non-research-title"
    return ""


def _parse_issue_page(
    content: bytes,
    issue_url: str,
    slug: str,
) -> dict[str, Any]:
    soup = BeautifulSoup(content, "html.parser")
    volume, issue, publication_date = _extract_issue_identity(soup)

    cards: list[Any] = []
    for selector in (
        ".al-article-item-wrap",
        ".al-article-items",
        "article.journal-article",
    ):
        cards = list(soup.select(selector))
        if cards:
            break

    article_items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    source_sequence = 0
    for card in cards:
        link = _first_article_link(card, issue_url, slug)
        if link is None:
            continue
        article_url = urljoin(issue_url, str(link.get("href") or ""))
        if article_url in seen_urls:
            continue
        seen_urls.add(article_url)
        source_sequence += 1
        title = _text(link)
        article_type = ""
        for selector in (
            ".al-article-type",
            ".article-type",
            "[data-article-type]",
        ):
            node = card.select_one(selector)
            if node:
                article_type = (
                    str(node.get("data-article-type") or "") or _text(node)
                )
                break
        doi = ""
        doi_link = card.select_one("a[href*='doi.org/']")
        if doi_link:
            doi = _normalize_doi(
                str(doi_link.get("href") or "") or _text(doi_link)
            )
        article_items.append(
            {
                "source_sequence": source_sequence,
                "title_en": title,
                "doi": doi,
                "source_url": article_url,
                "article_type": article_type or "journal-article",
                "exclusion_reason": _exclusion_reason(title, article_type),
            }
        )

    ancillary_items: list[dict[str, Any]] = []
    seen_ancillary: set[tuple[str, str]] = set()
    for link in soup.select("a[href*='/issue-pdf/']"):
        title = _text(link)
        reason = _exclusion_reason(title, "issue-matter")
        if not reason:
            continue
        url = urljoin(issue_url, str(link.get("href") or ""))
        key = (title.casefold(), url)
        if key in seen_ancillary:
            continue
        seen_ancillary.add(key)
        source_sequence += 1
        ancillary_items.append(
            {
                "source_sequence": source_sequence,
                "title_en": title,
                "reason": reason,
                "source_url": url,
            }
        )

    return {
        "volume": volume,
        "issue": issue,
        "publication_date": publication_date,
        "article_items": article_items,
        "ancillary_items": ancillary_items,
    }


def _extract_json_ld_fields(
    soup: BeautifulSoup,
) -> tuple[str, list[str], str, str, str]:
    for article in _json_ld_articles(soup):
        title = str(article.get("headline") or article.get("name") or "").strip()
        raw_authors = article.get("author") or []
        if not isinstance(raw_authors, list):
            raw_authors = [raw_authors]
        authors = []
        for raw_author in raw_authors:
            if isinstance(raw_author, dict):
                authors.append(str(raw_author.get("name") or ""))
            else:
                authors.append(str(raw_author))
        abstract = str(
            article.get("abstract") or article.get("description") or ""
        ).strip()
        doi = _normalize_doi(
            str(article.get("identifier") or article.get("sameAs") or "")
        )
        publication_date = str(article.get("datePublished") or "").strip()
        return title, _unique(authors), abstract, doi, publication_date
    return "", [], "", "", ""


def _parse_article_page(
    content: bytes,
    inventory_item: dict[str, Any],
) -> dict[str, Any]:
    soup = BeautifulSoup(content, "html.parser")
    json_title, json_authors, json_abstract, json_doi, json_date = (
        _extract_json_ld_fields(soup)
    )

    title_values = _meta_values(
        soup,
        "citation_title",
        "dc.title",
        "og:title",
    )
    title = title_values[0] if title_values else json_title
    if not title:
        for selector in (
            "h1.wi-article-title",
            "h1.article-title-main",
            "main h1",
            "h1",
        ):
            title = _text(soup.select_one(selector))
            if title:
                break
    title = title or inventory_item["title_en"]

    authors = _unique(
        _meta_values(soup, "citation_author", "dc.creator") or json_authors
    )
    if not authors:
        for selector in (
            ".info-card-author .info-card-name",
            ".al-authors-list .linked-name",
            ".al-authors-list a",
            ".author-name",
        ):
            authors = _unique([_text(node) for node in soup.select(selector)])
            if authors:
                break

    abstract = ""
    for selector in (
        "section.abstract",
        "section.article-abstract",
        ".abstractInFull",
        ".article-abstract",
        "[id='abstract']",
    ):
        node = soup.select_one(selector)
        if node:
            abstract = _text(node)
            abstract = re.sub(
                r"^Abstract\s*", "", abstract, flags=re.IGNORECASE
            )
            if abstract:
                break
    if not abstract:
        abstract = json_abstract
    if not abstract:
        values = _meta_values(soup, "citation_abstract", "dc.description")
        abstract = values[0] if values else ""

    doi_values = _meta_values(
        soup,
        "citation_doi",
        "dc.identifier",
    )
    doi = _normalize_doi(doi_values[0] if doi_values else json_doi)
    if not doi:
        doi = _normalize_doi(_text(soup))
    if not doi:
        doi = inventory_item["doi"]

    date_values = _meta_values(
        soup,
        "citation_publication_date",
        "dc.date",
    )
    publication_date = date_values[0] if date_values else json_date

    flags: list[str] = []
    if not doi:
        flags.append("doi_missing")
    if not authors:
        flags.append("authors_missing")
    if not abstract:
        flags.append("abstract_en_missing")
    flags.extend(["title_cn_missing", "abstract_cn_missing"])

    source_sequence = inventory_item["source_sequence"]
    source_url = inventory_item["source_url"]
    return {
        "paper_id": (
            f"doi:{doi}"
            if doi
            else f"oup:{urlparse(source_url).path.rsplit('/', 1)[-1]}"
        ),
        "sequence": source_sequence,
        "source_sequence": source_sequence,
        "article_type": "research-article",
        "title_en": title,
        "title_cn": "",
        "authors": authors,
        "abstract_en": abstract,
        "abstract_cn": "",
        "doi": doi,
        "source_url": source_url,
        "publication_date": publication_date,
        "sources": {
            "inventory": "official-issue-page",
            "title": "official-article-page",
            "authors": "official-article-page",
            "abstract": "official-article-page",
            "doi": "official-article-page",
        },
        "translation": {
            "status": "pending",
            "provider": "",
            "prompt_version": "",
            "glossary_version": "1",
        },
        "quality_flags": flags,
    }


def _detail_fallback(
    inventory_item: dict[str, Any],
    exc: requests.RequestException,
) -> dict[str, Any]:
    source_sequence = inventory_item["source_sequence"]
    source_url = inventory_item["source_url"]
    doi = inventory_item["doi"]
    flags = [f"detail_fetch_failed:{type(exc).__name__}"]
    if not doi:
        flags.append("doi_missing")
    flags.extend(
        [
            "authors_missing",
            "abstract_en_missing",
            "title_cn_missing",
            "abstract_cn_missing",
        ]
    )
    return {
        "paper_id": (
            f"doi:{doi}"
            if doi
            else f"oup:{urlparse(source_url).path.rsplit('/', 1)[-1]}"
        ),
        "sequence": source_sequence,
        "source_sequence": source_sequence,
        "article_type": "unknown",
        "title_en": inventory_item["title_en"],
        "title_cn": "",
        "authors": [],
        "abstract_en": "",
        "abstract_cn": "",
        "doi": doi,
        "source_url": source_url,
        "publication_date": "",
        "sources": {
            "inventory": "official-issue-page",
            "title": "official-issue-page",
            "doi": "official-issue-page",
        },
        "translation": {
            "status": "blocked",
            "provider": "",
            "prompt_version": "",
            "glossary_version": "1",
        },
        "quality_flags": flags,
    }


def _fetch_detail_safe(
    inventory_item: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = _get(_session(), inventory_item["source_url"])
        return _parse_article_page(response.content, inventory_item)
    except requests.RequestException as exc:
        return _detail_fallback(inventory_item, exc)


def fetch_current_issue(
    journal_or_url: str,
    current_issue_url: str | None = None,
) -> dict[str, Any]:
    """Fetch QJE or REStud from Oxford Academic's official current issue page.

    Accepted forms:
      fetch_current_issue("qje")
      fetch_current_issue("res")
      fetch_current_issue("restud", "https://academic.oup.com/restud/issue")
      fetch_current_issue("https://academic.oup.com/qje/issue")
    """

    spec, issue_url = _resolve_journal(journal_or_url, current_issue_url)
    response = _get(_session(), issue_url)
    parsed = _parse_issue_page(response.content, issue_url, spec["slug"])

    article_items = parsed["article_items"]
    research_items = [
        item for item in article_items if not item["exclusion_reason"]
    ]
    card_exclusions = [
        {
            "source_sequence": item["source_sequence"],
            "title_en": item["title_en"],
            "reason": item["exclusion_reason"],
            "source_url": item["source_url"],
        }
        for item in article_items
        if item["exclusion_reason"]
    ]
    excluded_items = sorted(
        card_exclusions + parsed["ancillary_items"],
        key=lambda item: item["source_sequence"],
    )

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        articles = list(pool.map(_fetch_detail_safe, research_items))

    for sequence, article in enumerate(articles, start=1):
        article["sequence"] = sequence

    source_sequences = [article["source_sequence"] for article in articles]
    expected_source_sequences = [
        item["source_sequence"] for item in research_items
    ]
    order_preserved = source_sequences == expected_source_sequences
    roster_match = len(articles) == len(research_items)

    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    translation_complete = sum(
        bool(article["title_cn"] and article["abstract_cn"])
        for article in articles
    )
    detail_failure_count = sum(
        any(
            flag.startswith("detail_fetch_failed:")
            for flag in article["quality_flags"]
        )
        for article in articles
    )

    flags: list[str] = []
    if not (parsed["volume"] and parsed["issue"]):
        flags.append("volume_issue_unparsed")
    if not article_items:
        flags.append("official_inventory_empty")
    if not roster_match:
        flags.append("official_inventory_mismatch")
    if not order_preserved:
        flags.append("official_order_mismatch")
    if detail_failure_count:
        flags.append("article_detail_fetch_incomplete")
    if translation_complete != len(articles):
        flags.append("translation_incomplete")
    if duplicate_count:
        flags.append("duplicate_doi")

    status = "ready" if not flags else "incomplete"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    volume = parsed["volume"]
    issue = parsed["issue"]
    issue_id = (
        f"{spec['journal_id']}-{volume or 'unknown'}-{issue or 'current'}"
    )

    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": spec["journal_id"],
        "journal_name": spec["journal_name"],
        "volume": volume,
        "issue": issue,
        "publication_date": parsed["publication_date"],
        "source_url": issue_url,
        "retrieved_at": now,
        "expected_article_count": len(research_items),
        "research_article_count": len(articles),
        "status": status,
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": roster_match,
            "order_preserved": order_preserved,
            "official_item_count": (
                len(article_items) + len(parsed["ancillary_items"])
            ),
            "excluded_item_count": len(excluded_items),
            "excluded_items": excluded_items,
            "detail_failure_count": detail_failure_count,
            "doi_complete": sum(bool(article["doi"]) for article in articles),
            "authors_complete": sum(
                bool(article["authors"]) for article in articles
            ),
            "abstract_en_complete": sum(
                bool(article["abstract_en"]) for article in articles
            ),
            "translation_complete": translation_complete,
            "duplicate_count": duplicate_count,
            "flags": flags,
        },
    }
