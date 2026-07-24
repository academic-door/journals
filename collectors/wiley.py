from __future__ import annotations

import email.utils
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


WILEY_HOST = "onlinelibrary.wiley.com"
JOURNAL_ID = "ecta"
JOURNAL_NAME = "Econometrica"
USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
DEFAULT_TIMEOUT = (10, 35)
DEFAULT_ATTEMPTS = 4
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"'<>?&#]+", re.IGNORECASE)
VOLUME_ISSUE_PATTERNS = (
    re.compile(
        r"\bVolume\s+(?P<volume>\d+)\s*,?\s*Issue\s+(?P<issue>[\w.-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bVol\.?\s*(?P<volume>\d+)\s*,?\s*(?:No\.?|Issue)\s*"
        r"(?P<issue>[\w.-]+)",
        re.IGNORECASE,
    ),
)
MONTH_YEAR_PATTERN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{4}\b",
    re.IGNORECASE,
)
CHALLENGE_MARKERS = (
    "cf-chl-",
    'id="challenge-form"',
    "verify you are human",
    "request unsuccessful",
    "enable javascript and cookies to continue",
    "<title>just a moment",
    "<title>access denied",
)
ITEM_CLASSES = {
    "issue-item",
    "toc-item",
    "issue-article",
    "issue-item-wrapper",
}
SECTION_CLASS_MARKERS = (
    "issue-item__header",
    "issue-section",
    "section-title",
    "toc-heading",
    "toc-section",
)
ACTION_LINK_TEXT = {
    "abstract",
    "full text",
    "pdf",
    "references",
    "request permissions",
    "first page",
    "export citation",
}


class WileyCollectorError(RuntimeError):
    """Base class for machine-readable Wiley collection failures."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        url: str,
        attempts: int = 0,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.url = url
        self.attempts = attempts
        self.status_code = status_code


class WileyFetchError(WileyCollectorError):
    """Network, HTTP, redirect, or publisher challenge failure."""


class WileyParseError(WileyCollectorError):
    """The official page was fetched but its inventory could not be audited."""


@dataclass(frozen=True)
class InventoryItem:
    source_sequence: int
    section: str
    title: str
    source_url: str
    doi: str
    authors: tuple[str, ...]
    exclusion_reason: str

    @property
    def is_research_article(self) -> bool:
        return not self.exclusion_reason


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


def _text(node: Any) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _clean_meta(value: str) -> str:
    return " ".join((value or "").split())


def _meta_values(soup: BeautifulSoup, names: Iterable[str]) -> list[str]:
    wanted = {name.casefold() for name in names}
    values: list[str] = []
    for node in soup.select("meta[content]"):
        key = (
            node.get("name")
            or node.get("property")
            or node.get("itemprop")
            or ""
        ).casefold()
        if key in wanted:
            value = _clean_meta(node.get("content", ""))
            if value:
                values.append(value)
    return values


def _first_meta(soup: BeautifulSoup, names: Iterable[str]) -> str:
    values = _meta_values(soup, names)
    return values[0] if values else ""


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _normalise_doi(value: str) -> str:
    match = DOI_PATTERN.search(value or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,;:)]}").lower()


def _is_official_wiley_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").casefold() == WILEY_HOST


def _require_official_wiley_url(url: str) -> None:
    if not _is_official_wiley_url(url):
        raise WileyFetchError(
            "non_official_url",
            "Only official Wiley Online Library HTTPS pages may be fetched.",
            url=url,
        )


def _canonical_article_url(href: str, base_url: str, doi: str = "") -> str:
    absolute = urljoin(base_url, href or "")
    doi_value = _normalise_doi(doi or absolute)
    if _is_official_wiley_url(absolute) and "/doi/" in urlparse(absolute).path:
        if doi_value:
            return f"https://{WILEY_HOST}/doi/{doi_value}"
        return absolute.split("?", 1)[0].split("#", 1)[0]
    if doi_value:
        return f"https://{WILEY_HOST}/doi/{doi_value}"
    return ""


def _retry_after_seconds(response: requests.Response, attempt: int) -> float:
    raw_value = response.headers.get("Retry-After", "").strip()
    if raw_value:
        try:
            return min(max(float(raw_value), 0.0), 60.0)
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(raw_value)
                now = datetime.now(retry_at.tzinfo or timezone.utc)
                return min(max((retry_at - now).total_seconds(), 0.0), 60.0)
            except (TypeError, ValueError, OverflowError):
                pass
    return min(1.5 * (2 ** (attempt - 1)), 12.0)


def _looks_like_challenge(response: requests.Response) -> bool:
    sample = response.content[:20000].decode("utf-8", errors="ignore").casefold()
    return any(marker in sample for marker in CHALLENGE_MARKERS)


def _http_error_code(status_code: int) -> str:
    if status_code in {401, 403, 429}:
        return "publisher_access_blocked"
    if status_code == 404:
        return "publisher_page_not_found"
    if 400 <= status_code < 500:
        return "publisher_client_error"
    if status_code >= 500:
        return "publisher_server_error"
    return "publisher_http_error"


def _get(
    session: requests.Session,
    url: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> requests.Response:
    _require_official_wiley_url(url)
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last_error: WileyFetchError | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=timeout)
        except requests.Timeout as exc:
            last_error = WileyFetchError(
                "publisher_timeout",
                "Wiley did not respond before the configured timeout.",
                url=url,
                attempts=attempt,
            )
            if attempt < attempts:
                sleep_fn(min(1.5 * (2 ** (attempt - 1)), 12.0))
                continue
            raise last_error from exc
        except requests.RequestException as exc:
            last_error = WileyFetchError(
                "publisher_connection_error",
                "The official Wiley page could not be reached.",
                url=url,
                attempts=attempt,
            )
            if attempt < attempts:
                sleep_fn(min(1.5 * (2 ** (attempt - 1)), 12.0))
                continue
            raise last_error from exc

        resolved_url = getattr(response, "url", url) or url
        if not _is_official_wiley_url(resolved_url):
            raise WileyFetchError(
                "non_official_redirect",
                "Wiley redirected the request away from its official host.",
                url=resolved_url,
                attempts=attempt,
                status_code=response.status_code,
            )

        status_code = response.status_code
        if 200 <= status_code < 300:
            if _looks_like_challenge(response):
                last_error = WileyFetchError(
                    "publisher_access_blocked",
                    "Wiley returned an anti-bot or browser-verification page.",
                    url=resolved_url,
                    attempts=attempt,
                    status_code=status_code,
                )
                if attempt < attempts:
                    sleep_fn(min(1.5 * (2 ** (attempt - 1)), 12.0))
                    continue
                raise last_error
            return response

        last_error = WileyFetchError(
            _http_error_code(status_code),
            f"Wiley returned HTTP {status_code}.",
            url=resolved_url,
            attempts=attempt,
            status_code=status_code,
        )
        if status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
            sleep_fn(_retry_after_seconds(response, attempt))
            continue
        if status_code in {401, 403} and attempt < attempts:
            sleep_fn(min(1.5 * (2 ** (attempt - 1)), 12.0))
            continue
        raise last_error

    assert last_error is not None
    raise last_error


def _tag_classes(node: Tag | None) -> set[str]:
    if not isinstance(node, Tag):
        return set()
    return {str(value).casefold() for value in node.get("class", [])}


def _is_item_node(node: Tag | None) -> bool:
    if not isinstance(node, Tag):
        return False
    if _tag_classes(node) & ITEM_CLASSES:
        return True
    return (node.get("data-testid") or "").casefold() in {
        "issue-item",
        "toc-item",
    }


def _has_item_ancestor(node: Tag) -> bool:
    return any(_is_item_node(parent) for parent in node.parents if isinstance(parent, Tag))


def _heading_has_section_hint(node: Tag) -> bool:
    own_classes = " ".join(_tag_classes(node))
    parent_classes = " ".join(_tag_classes(node.parent if isinstance(node.parent, Tag) else None))
    class_text = f"{own_classes} {parent_classes}"
    return any(marker in class_text for marker in SECTION_CLASS_MARKERS)


def _normalise_section(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _is_original_articles_section(section: str) -> bool:
    return bool(re.fullmatch(r"original articles?", _normalise_section(section)))


def _section_exclusion_reason(section: str, title: str) -> str:
    section_text = _normalise_section(section)
    title_text = _normalise_section(title)
    if re.search(r"\bfront\s*matter\b|\bfrontmatter\b|\bprelims?\b", title_text):
        return "front-matter"
    if re.search(r"\bback\s*matter\b|\bbackmatter\b", title_text):
        return "back-matter"
    if _is_original_articles_section(section):
        return ""
    if re.search(
        r"\bfront\s*matter\b|\bfrontmatter\b|\bprelims?\b|\bpreliminaries\b",
        section_text,
    ):
        return "front-matter"
    if re.search(r"\bback\s*matter\b|\bbackmatter\b", section_text):
        return "back-matter"
    if not section_text:
        return "section-unparsed"
    return "section-not-original-articles"


def _looks_like_section_heading(node: Tag) -> bool:
    if node.name not in {"h2", "h3", "h4"} or _has_item_ancestor(node):
        return False
    value = _text(node)
    if not value or len(value) > 180:
        return False
    normalised = _normalise_section(value)
    known = (
        _is_original_articles_section(value)
        or "front matter" in normalised
        or "frontmatter" in normalised
        or "prelim" in normalised
        or "back matter" in normalised
        or "backmatter" in normalised
    )
    return known or _heading_has_section_hint(node)


def _first_text(node: Tag, selectors: Iterable[str]) -> str:
    for selector in selectors:
        value = _text(node.select_one(selector))
        if value:
            return value
    return ""


def _item_title_and_link(node: Tag) -> tuple[str, str]:
    selectors = (
        ".issue-item__title a[href]",
        ".toc-item__title a[href]",
        ".citation__title a[href]",
        "h2 a[href*='/doi/']",
        "h3 a[href*='/doi/']",
        "h4 a[href*='/doi/']",
        "a[href*='/doi/']",
    )
    for selector in selectors:
        link = node.select_one(selector)
        title = _text(link)
        if link and title and title.casefold() not in ACTION_LINK_TEXT:
            return title, link.get("href", "")
    title = _first_text(
        node,
        (
            ".issue-item__title",
            ".toc-item__title",
            ".citation__title",
            "h2",
            "h3",
            "h4",
        ),
    )
    return title, ""


def _item_authors(node: Tag) -> tuple[str, ...]:
    selectors = (
        ".loa .author",
        ".loa a",
        ".issue-item__authors a",
        ".toc-item__authors a",
        ".authors a",
        "[data-testid='author-name']",
    )
    for selector in selectors:
        values = _deduplicate(_text(author) for author in node.select(selector))
        if values:
            return tuple(values)
    return ()


def _item_section(node: Tag, inherited_section: str) -> str:
    local = _first_text(
        node,
        (
            ".issue-item__type",
            ".toc-item__type",
            ".article-type",
            "[data-testid='article-type']",
        ),
    )
    return local or inherited_section


def _inventory_from_node(
    node: Tag,
    *,
    source_sequence: int,
    section: str,
    base_url: str,
) -> InventoryItem:
    title, href = _item_title_and_link(node)
    raw_doi = (
        node.get("data-doi")
        or node.get("data-item-doi")
        or href
        or _text(node)
    )
    doi = _normalise_doi(str(raw_doi))
    source_url = _canonical_article_url(href, base_url, doi)
    effective_section = _item_section(node, section)
    exclusion_reason = _section_exclusion_reason(effective_section, title)
    if not title:
        title = "(unparsed official item)"
        exclusion_reason = exclusion_reason or "inventory-item-unparsed"
    if not source_url and not exclusion_reason:
        exclusion_reason = "official-article-link-unparsed"
    return InventoryItem(
        source_sequence=source_sequence,
        section=effective_section,
        title=title,
        source_url=source_url or base_url,
        doi=doi,
        authors=_item_authors(node),
        exclusion_reason=exclusion_reason,
    )


def _fallback_anchor_inventory(
    soup: BeautifulSoup, base_url: str
) -> tuple[list[InventoryItem], bool]:
    items: list[InventoryItem] = []
    seen: set[str] = set()
    current_section = ""
    saw_original_section = False
    selector = "h2, h3, h4, a[href*='/doi/']"
    for node in soup.select(selector):
        if node.name in {"h2", "h3", "h4"} and _looks_like_section_heading(node):
            current_section = _text(node)
            saw_original_section = saw_original_section or _is_original_articles_section(
                current_section
            )
            continue
        if node.name != "a":
            continue
        title = _text(node)
        if not title or title.casefold() in ACTION_LINK_TEXT:
            continue
        doi = _normalise_doi(node.get("href", ""))
        canonical_url = _canonical_article_url(node.get("href", ""), base_url, doi)
        identity = doi or canonical_url
        if not identity or identity in seen:
            continue
        seen.add(identity)
        reason = _section_exclusion_reason(current_section, title)
        items.append(
            InventoryItem(
                source_sequence=len(items) + 1,
                section=current_section,
                title=title,
                source_url=canonical_url,
                doi=doi,
                authors=(),
                exclusion_reason=reason,
            )
        )
    return items, saw_original_section


def _parse_volume_issue(soup: BeautifulSoup) -> tuple[str, str]:
    volume = _first_meta(soup, ("citation_volume", "prism.volume"))
    issue = _first_meta(soup, ("citation_issue", "prism.number"))
    if volume and issue:
        return volume, issue

    candidates = [
        _text(node)
        for node in soup.select(
            "h1, .issue-header__title, .issue-header, .issue-title"
        )
    ]
    candidates.extend(
        _meta_values(soup, ("og:title", "twitter:title", "citation_title"))
    )
    for candidate in candidates:
        for pattern in VOLUME_ISSUE_PATTERNS:
            match = pattern.search(candidate)
            if match:
                return match.group("volume"), match.group("issue")
    return volume, issue


def _parse_issue_date(soup: BeautifulSoup) -> str:
    meta_date = _first_meta(
        soup,
        (
            "citation_publication_date",
            "prism.publicationdate",
            "dc.date",
        ),
    )
    if meta_date:
        return meta_date
    for selector in (
        ".issue-header__date",
        ".issue-header__details",
        ".cover-date",
        ".coverDate",
        ".issue-date",
    ):
        value = _text(soup.select_one(selector))
        match = MONTH_YEAR_PATTERN.search(value)
        if match:
            return match.group(0)
    return ""


def _parse_issue_inventory(
    content: bytes, base_url: str
) -> tuple[str, str, str, list[InventoryItem]]:
    soup = BeautifulSoup(content, "html.parser")
    volume, issue = _parse_volume_issue(soup)
    publication_date = _parse_issue_date(soup)
    items: list[InventoryItem] = []
    current_section = ""
    saw_original_section = False

    selector = (
        "h2, h3, h4, article.issue-item, div.issue-item, li.issue-item, "
        "article.toc-item, div.toc-item, li.toc-item, "
        "[data-testid='issue-item'], [data-testid='toc-item']"
    )
    for node in soup.select(selector):
        if _is_item_node(node):
            if _has_item_ancestor(node):
                continue
            item = _inventory_from_node(
                node,
                source_sequence=len(items) + 1,
                section=current_section,
                base_url=base_url,
            )
            saw_original_section = saw_original_section or _is_original_articles_section(
                item.section
            )
            items.append(item)
            continue
        if _looks_like_section_heading(node):
            current_section = _text(node)
            saw_original_section = saw_original_section or _is_original_articles_section(
                current_section
            )

    if not items:
        items, fallback_saw_original = _fallback_anchor_inventory(soup, base_url)
        saw_original_section = saw_original_section or fallback_saw_original

    if not saw_original_section:
        raise WileyParseError(
            "original_articles_section_missing",
            "The official issue page did not expose an auditable Original Articles section.",
            url=base_url,
        )
    if not items:
        raise WileyParseError(
            "official_issue_inventory_empty",
            "No official issue items could be parsed.",
            url=base_url,
        )
    if not any(item.is_research_article for item in items):
        raise WileyParseError(
            "original_articles_inventory_empty",
            "The Original Articles section contained no parseable article links.",
            url=base_url,
        )
    return volume, issue, publication_date, items


def _parse_article_page(content: bytes, source_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(content, "html.parser")
    title = _first_meta(soup, ("citation_title", "dc.title", "og:title"))
    if not title:
        title = _first_text(
            soup,
            (
                "h1.article-header__title",
                "h1.citation__title",
                "h1",
            ),
        )

    authors = _meta_values(
        soup,
        (
            "citation_author",
            "dc.creator",
            "prism.author",
        ),
    )
    if not authors:
        for selector in (
            ".article-header__authors .author-name",
            ".loa .author",
            ".authors .author",
            "[data-testid='author-name']",
        ):
            authors = _deduplicate(_text(node) for node in soup.select(selector))
            if authors:
                break

    abstract = _first_meta(soup, ("citation_abstract", "dc.description"))
    if not abstract:
        abstract = _first_text(
            soup,
            (
                "section.article-section__abstract .article-section__content",
                ".article-section__abstract .article-section__content",
                ".abstract-group .article-section__content",
                "section.abstract .section__content",
                "section.abstract",
                ".article__abstract",
                "[property='abstract']",
            ),
        )
    abstract = re.sub(r"^\s*Abstract\s*", "", abstract, flags=re.IGNORECASE).strip()

    doi = _normalise_doi(
        _first_meta(
            soup,
            (
                "citation_doi",
                "prism.doi",
                "dc.identifier",
            ),
        )
        or source_url
    )
    volume, issue = _parse_volume_issue(soup)
    publication_date = _first_meta(
        soup,
        (
            "citation_publication_date",
            "prism.publicationdate",
            "dc.date",
        ),
    )
    article_type = _first_meta(
        soup,
        (
            "citation_section",
            "prism.section",
            "dc.type",
        ),
    )
    if not article_type:
        article_type = _first_text(
            soup,
            (
                ".article-header__category",
                ".article-type",
                "[data-testid='article-type']",
            ),
        )
    return {
        "title": title,
        "authors": _deduplicate(authors),
        "abstract": abstract,
        "doi": doi,
        "volume": volume,
        "issue": issue,
        "publication_date": publication_date,
        "article_type": article_type,
    }


def _article_from_inventory(
    item: InventoryItem,
    detail: dict[str, Any] | None,
    *,
    issue_volume: str,
    issue_number: str,
    error: WileyCollectorError | None = None,
) -> dict[str, Any]:
    detail = detail or {}
    title = detail.get("title") or item.title
    authors = detail.get("authors") or list(item.authors)
    abstract = detail.get("abstract") or ""
    doi = detail.get("doi") or item.doi
    publication_date = detail.get("publication_date") or ""
    flags: list[str] = []
    if error:
        flags.append(f"detail_fetch_failed:{error.code}")
    if detail.get("doi") and item.doi and detail["doi"] != item.doi:
        flags.append("detail_doi_mismatch")
    if detail.get("volume") and issue_volume and detail["volume"] != issue_volume:
        flags.append("detail_volume_mismatch")
    if detail.get("issue") and issue_number and detail["issue"] != issue_number:
        flags.append("detail_issue_mismatch")
    if detail.get("article_type") and not _is_original_articles_section(
        detail["article_type"]
    ):
        flags.append("detail_article_type_mismatch")
    if not doi:
        flags.append("doi_missing")
    if not authors:
        flags.append("authors_missing")
    if not abstract:
        flags.append("abstract_en_missing")
    flags.extend(("title_cn_missing", "abstract_cn_missing"))

    title_source = "official-article-page" if detail.get("title") else "official-issue-page"
    authors_source = (
        "official-article-page" if detail.get("authors") else "official-issue-page"
    )
    doi_source = "official-article-page" if detail.get("doi") else "official-issue-page"
    return {
        "paper_id": f"doi:{doi}" if doi else f"wiley:{item.source_sequence}",
        "sequence": 0,
        "source_sequence": item.source_sequence,
        "article_type": "research-article",
        "title_en": title,
        "title_cn": "",
        "authors": list(authors),
        "abstract_en": abstract,
        "abstract_cn": "",
        "doi": doi,
        "source_url": item.source_url,
        "publication_date": publication_date,
        "sources": {
            "inventory": "official-issue-page",
            "section": "official-issue-page",
            "title": title_source,
            "authors": authors_source,
            "abstract": (
                "official-article-page" if abstract else "official-article-page-missing"
            ),
            "doi": doi_source,
            "volume": "official-issue-page",
            "issue": "official-issue-page",
        },
        "translation": {
            "status": "pending" if not error else "blocked",
            "provider": "",
            "prompt_version": "",
            "glossary_version": "1",
        },
        "quality_flags": flags,
    }


def _fetch_article_safe(
    item: InventoryItem,
    *,
    issue_volume: str,
    issue_number: str,
    attempts: int,
    timeout: tuple[float, float],
    sleep_fn: Callable[[float], None],
) -> tuple[dict[str, Any], WileyCollectorError | None]:
    try:
        response = _get(
            _session(),
            item.source_url,
            attempts=attempts,
            timeout=timeout,
            sleep_fn=sleep_fn,
        )
        detail = _parse_article_page(response.content, item.source_url)
        return (
            _article_from_inventory(
                item,
                detail,
                issue_volume=issue_volume,
                issue_number=issue_number,
            ),
            None,
        )
    except WileyCollectorError as exc:
        return (
            _article_from_inventory(
                item,
                None,
                issue_volume=issue_volume,
                issue_number=issue_number,
                error=exc,
            ),
            exc,
        )
    except Exception as exc:  # Defensive boundary for publisher markup drift.
        error = WileyParseError(
            "article_page_parse_failed",
            f"Unexpected article page parser failure: {type(exc).__name__}",
            url=item.source_url,
        )
        return (
            _article_from_inventory(
                item,
                None,
                issue_volume=issue_volume,
                issue_number=issue_number,
                error=error,
            ),
            error,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _error_issue(
    source_url: str,
    error: WileyCollectorError,
    *,
    retrieved_at: str,
) -> dict[str, Any]:
    error_data: dict[str, Any] = {
        "code": error.code,
        "attempts": error.attempts,
    }
    if error.status_code is not None:
        error_data["http_status"] = error.status_code
    return {
        "schema_version": "1.0",
        "issue_id": f"{JOURNAL_ID}-unknown-current",
        "journal_id": JOURNAL_ID,
        "journal_name": JOURNAL_NAME,
        "volume": "",
        "issue": "",
        "publication_date": "",
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "expected_article_count": 0,
        "research_article_count": 0,
        "status": "error",
        "development_sample": False,
        "articles": [],
        "quality": {
            "roster_match": False,
            "order_preserved": False,
            "official_item_count": 0,
            "official_research_item_count": 0,
            "excluded_item_count": 0,
            "excluded_items": [],
            "detail_failure_count": 0,
            "doi_complete": 0,
            "authors_complete": 0,
            "abstract_en_complete": 0,
            "translation_complete": 0,
            "duplicate_count": 0,
            "error": error_data,
            "flags": [f"collector_error:{error.code}"],
        },
    }


def fetch_current_issue(
    current_issue_url: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    max_workers: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Collect an Econometrica issue from its official Wiley issue and article pages.

    The default URL is expected to be the journal's official ``/toc/.../current``
    page.  Tests may pass a concrete official issue URL.  Issue membership,
    section classification, and order always come from that official issue page.
    """

    now = retrieved_at or _now_iso()
    try:
        _require_official_wiley_url(current_issue_url)
        response = _get(
            _session(),
            current_issue_url,
            attempts=attempts,
            timeout=timeout,
            sleep_fn=sleep_fn,
        )
        resolved_url = getattr(response, "url", current_issue_url) or current_issue_url
        volume, issue, publication_date, inventory = _parse_issue_inventory(
            response.content, resolved_url
        )
    except WileyCollectorError as exc:
        return _error_issue(current_issue_url, exc, retrieved_at=now)

    research_inventory = [item for item in inventory if item.is_research_article]
    excluded_items = [
        {
            "source_sequence": item.source_sequence,
            "title_en": item.title,
            "section": item.section,
            "reason": item.exclusion_reason,
            "source_url": item.source_url,
        }
        for item in inventory
        if not item.is_research_article
    ]

    worker_count = max(1, min(max_workers, 8))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(
            pool.map(
                lambda item: _fetch_article_safe(
                    item,
                    issue_volume=volume,
                    issue_number=issue,
                    attempts=attempts,
                    timeout=timeout,
                    sleep_fn=sleep_fn,
                ),
                research_inventory,
            )
        )

    articles = [article for article, _error in results]
    errors = [error for _article, error in results if error is not None]
    for sequence, article in enumerate(articles, start=1):
        article["sequence"] = sequence

    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    doi_complete = sum(bool(article["doi"]) for article in articles)
    authors_complete = sum(bool(article["authors"]) for article in articles)
    abstract_en_complete = sum(bool(article["abstract_en"]) for article in articles)
    translation_complete = sum(
        bool(article["title_cn"] and article["abstract_cn"]) for article in articles
    )
    roster_match = len(articles) == len(research_inventory)
    source_sequences = [article["source_sequence"] for article in articles]
    order_preserved = source_sequences == sorted(source_sequences)

    flags: list[str] = []
    if not volume or not issue:
        flags.append("volume_issue_unparsed")
    if not roster_match:
        flags.append("official_inventory_mismatch")
    if not order_preserved:
        flags.append("official_order_mismatch")
    if errors:
        flags.append("article_detail_fetch_incomplete")
    if doi_complete != len(articles):
        flags.append("doi_incomplete")
    if authors_complete != len(articles):
        flags.append("authors_incomplete")
    if abstract_en_complete != len(articles):
        flags.append("abstract_en_incomplete")
    if translation_complete != len(articles):
        flags.append("translation_incomplete")
    if duplicate_count:
        flags.append("duplicate_doi")
    if any(
        flag in {"detail_volume_mismatch", "detail_issue_mismatch"}
        for article in articles
        for flag in article["quality_flags"]
    ):
        flags.append("article_issue_metadata_mismatch")
    if any(
        flag == "detail_article_type_mismatch"
        for article in articles
        for flag in article["quality_flags"]
    ):
        flags.append("article_type_metadata_mismatch")

    issue_id = f"{JOURNAL_ID}-{volume or 'unknown'}-{issue or 'current'}"
    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": JOURNAL_ID,
        "journal_name": JOURNAL_NAME,
        "volume": volume,
        "issue": issue,
        "publication_date": publication_date,
        "source_url": current_issue_url,
        "retrieved_at": now,
        "expected_article_count": len(research_inventory),
        "research_article_count": len(articles),
        "status": "ready" if not flags else "incomplete",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": roster_match,
            "order_preserved": order_preserved,
            "official_item_count": len(inventory),
            "official_research_item_count": len(research_inventory),
            "excluded_item_count": len(excluded_items),
            "excluded_items": excluded_items,
            "detail_failure_count": len(errors),
            "detail_failures": [
                {
                    "source_sequence": item.source_sequence,
                    "code": error.code,
                    "attempts": error.attempts,
                    **(
                        {"http_status": error.status_code}
                        if error.status_code is not None
                        else {}
                    ),
                }
                for item, error in zip(
                    research_inventory,
                    [error for _article, error in results],
                )
                if error is not None
            ],
            "doi_complete": doi_complete,
            "authors_complete": authors_complete,
            "abstract_en_complete": abstract_en_complete,
            "translation_complete": translation_complete,
            "duplicate_count": duplicate_count,
            "flags": flags,
        },
    }
