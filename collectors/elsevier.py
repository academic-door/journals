from __future__ import annotations

import html
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
REQUEST_TIMEOUT = (10, 60)
MAX_ATTEMPTS = 4
DETAIL_WORKERS = 6
ROOT = Path(__file__).resolve().parents[1]
ORDER_OVERRIDES = ROOT / "data" / "order-overrides"
ISSUE_HEADING = re.compile(
    r"(?P<year>\d{4}),\s*Volume\s+(?P<volume>[A-Za-z0-9.-]+),\s*Issue\s+(?P<issue>[A-Za-z0-9.-]+)",
    re.IGNORECASE,
)
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"'<>?&#]+", re.IGNORECASE)
PII_PATTERN = re.compile(r"S\d{15}[0-9X]", re.IGNORECASE)
NON_RESEARCH_PATTERN = re.compile(
    r"editorial\s+board|corrigendum|correction|erratum|retraction|"
    r"front\s*matter|back\s*matter|table\s+of\s+contents",
    re.IGNORECASE,
)


class ElsevierCollectorError(RuntimeError):
    pass


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json",
            "Accept-Language": "en-US,en;q=0.8",
        }
    )
    return session


def _get(
    session: requests.Session,
    url: str,
    *,
    attempts: int = MAX_ATTEMPTS,
    patient_403: bool = False,
) -> requests.Response:
    """GET with retry; optionally back off patiently on ScienceDirect 403.

    ScienceDirect anti-bot blocks are often intermittent. patient_403 waits
    much longer between retries so a later attempt can land in a fresh
    request window, while all other failures keep the short backoff.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt + 1 < attempts:
                status_code = (
                    error.response.status_code
                    if isinstance(error, requests.HTTPError)
                    and error.response is not None
                    else 0
                )
                if patient_403 and status_code == 403:
                    time.sleep(5 + 5 * attempt)
                else:
                    time.sleep(1.5 * (attempt + 1))
    raise ElsevierCollectorError(f"request failed for {url}: {last_error}")


def _clean(value: str) -> str:
    return " ".join(BeautifulSoup(html.unescape(value or ""), "html.parser").get_text(" ", strip=True).split())


def _meta(soup: BeautifulSoup, name: str) -> str:
    wanted = name.casefold()
    for node in soup.find_all("meta"):
        key = str(node.get("name") or node.get("property") or "").casefold()
        if key == wanted:
            value = str(node.get("content") or "").strip()
            if value:
                return value
    return ""


def _normalize_doi(value: str) -> str:
    match = DOI_PATTERN.search(value or "")
    return match.group(0).rstrip(".,);]").lower() if match else ""


def _normalize_authors(value: str) -> list[str]:
    raw_names = re.split(r"\s*(?:;|&)\s*", value or "")
    names: list[str] = []
    for raw in raw_names:
        raw = " ".join(raw.split()).strip(" ,")
        if not raw:
            continue
        if "," in raw:
            family, given = (part.strip() for part in raw.split(",", 1))
            name = " ".join(part for part in (given, family) if part)
        else:
            name = raw
        if name and name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
    return names


def _article_type(title: str, raw_type: str = "") -> str:
    combined = f"{raw_type} {title}"
    if re.search(r"\bcomment\b|\breply\b|\bdiscussion\b", combined, re.IGNORECASE):
        return "comment"
    return "research-article"


def _source_pii(value: str) -> str:
    match = PII_PATTERN.search(value or "")
    return match.group(0).upper() if match else ""


def _repec_item_id(value: str) -> str:
    match = re.search(r"/([^/]+)\.html(?:$|\?)", value or "")
    return match.group(1) if match else ""


def _official_article_url(pii: str, fallback: str) -> str:
    return f"https://www.sciencedirect.com/science/article/pii/{pii}" if pii else fallback


def _parse_repec_inventory(content: bytes, series_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(content, "html.parser")
    heading = next(
        (
            node
            for node in soup.find_all("h3")
            if ISSUE_HEADING.search(node.get_text(" ", strip=True))
        ),
        None,
    )
    if heading is None:
        raise ElsevierCollectorError("RePEc serial page has no usable volume heading")
    match = ISSUE_HEADING.search(heading.get_text(" ", strip=True))
    assert match is not None
    container = heading.find_next_sibling("div")
    if container is None:
        raise ElsevierCollectorError("RePEc serial page has no issue article container")
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in container.select("a[href^='/a/']"):
        title = _clean(link.get_text(" ", strip=True))
        detail_url = urljoin(series_url, str(link.get("href") or ""))
        pii = _source_pii(detail_url)
        key = pii or detail_url
        if not title or key in seen:
            continue
        seen.add(key)
        items.append({"title_en": title, "detail_url": detail_url, "pii": pii})
    if not items:
        raise ElsevierCollectorError("RePEc serial page returned an empty issue")
    return {
        "year": match.group("year"),
        "volume": match.group("volume"),
        "issue": match.group("issue"),
        "items": items,
    }


def _parse_repec_detail(
    session: requests.Session,
    item: dict[str, str],
    doi_template: str = "",
) -> dict[str, Any]:
    response = _get(session, item["detail_url"])
    soup = BeautifulSoup(response.content, "html.parser")
    title = _clean(_meta(soup, "citation_title") or _meta(soup, "title") or item["title_en"])
    abstract = _clean(_meta(soup, "citation_abstract"))
    if re.fullmatch(
        r"no abstract (?:is )?available(?: for this item)?\.?",
        abstract,
        flags=re.IGNORECASE,
    ):
        abstract = ""
    authors = _normalize_authors(
        _meta(soup, "citation_authors") or _meta(soup, "author")
    )
    publication_date = _repec_citation_date(soup)
    doi = _normalize_doi(_meta(soup, "DOI") or soup.get_text(" ", strip=True))
    if not doi and doi_template:
        item_id = _repec_item_id(item["detail_url"])
        if item_id:
            doi = _normalize_doi(doi_template.format(id=item_id))
    pii = item["pii"] or _source_pii(_meta(soup, "handle"))
    flags: list[str] = []
    if not doi:
        flags.append("doi_missing")
    if not authors:
        flags.append("authors_missing")
    if not abstract:
        flags.append("abstract_en_missing")
    flags.extend(["title_cn_missing", "abstract_cn_missing"])
    return {
        "pii": pii,
        "title_en": title,
        "authors": authors,
        "abstract_en": abstract,
        "doi": doi,
        "source_url": _official_article_url(pii, item["detail_url"]),
        "detail_url": item["detail_url"],
        "article_type": _article_type(title),
        "publication_date": publication_date,
        "quality_flags": flags,
    }


def _repec_page_url(series_url: str, page: int) -> str:
    """RePEc series pages paginate as stem.html, stem2.html, stem3.html..."""

    if page <= 1:
        return series_url
    parsed = urlparse(series_url)
    stem = Path(parsed.path).stem
    suffix = Path(parsed.path).suffix
    return urljoin(
        series_url,
        f"{stem}{page}{suffix}",
    )


def _parse_repec_volume_sections(
    content: bytes,
    series_url: str,
) -> dict[str, dict[str, Any]]:
    """Parse every volume heading on one RePEc series page."""

    soup = BeautifulSoup(content, "html.parser")
    found: dict[str, dict[str, Any]] = {}
    for heading in soup.find_all("h3"):
        match = ISSUE_HEADING.search(heading.get_text(" ", strip=True))
        if not match:
            continue
        volume = match.group("volume")
        container = heading.find_next_sibling("div")
        if container is None:
            continue
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for link in container.select("a[href^='/a/']"):
            title = _clean(link.get_text(" ", strip=True))
            detail_url = urljoin(series_url, str(link.get("href") or ""))
            pii = _source_pii(detail_url)
            key = pii or detail_url
            if not title or key in seen:
                continue
            seen.add(key)
            items.append(
                {"title_en": title, "detail_url": detail_url, "pii": pii}
            )
        if items:
            found[volume] = {
                "year": match.group("year"),
                "issue": match.group("issue"),
                "items": items,
            }
    return found


def fetch_elsevier_repec_history_issue(
    *,
    journal_id: str,
    journal_name: str,
    issn: str,
    volume: str,
    repec_series_url: str,
    doi_template: str = "",
    max_pages: int = 40,
    session: requests.Session | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Build one historical Elsevier issue from the RePEc serial archive.

    Crossref coverage of Elsevier continuous volumes can be incomplete (e.g.
    JDE Vol. 173 has 7 items in Crossref but 12 on RePEc). RePEc mirrors the
    publisher's per-volume article lists on paginated series pages and exposes
    title/authors/abstract on each article page, so it is used as the roster
    authority for history; missing abstracts fall back to the Elsevier API.
    """

    client = session or _session()
    target: dict[str, Any] | None = None
    for page in range(1, max_pages + 1):
        url = _repec_page_url(repec_series_url, page)
        content = _get(
            client,
            url,
            attempts=3,
            patient_403=True,
        ).content
        sections = _parse_repec_volume_sections(content, repec_series_url)
        if str(volume) in sections:
            target = sections[str(volume)]
            break
        if not sections and page > 1:
            # Empty page means the pagination ended before the volume.
            break
    if target is None:
        raise ValueError(f"RePEc archive has no volume {volume}")

    def build_article(entry: dict[str, str]) -> dict[str, Any]:
        detail = _parse_repec_detail(
            client,
            entry,
            doi_template=doi_template,
        )
        pii = detail.get("pii", "") or entry.get("pii", "")
        doi = detail.get("doi", "")
        abstract = str(detail.get("abstract_en", "")).strip()
        abstract_source = (
            "repec-publisher-supplied" if abstract else ""
        )
        from collectors.metadata_fallback import _is_elsevier_identifier

        if not abstract and _is_elsevier_identifier(pii, doi):
            from collectors.metadata_fallback import _elsevier_lookup

            lookup = _elsevier_lookup(client, pii, doi=doi, timeout=timeout)
            fetched = str(lookup.get("abstract", "")).strip()
            if fetched:
                abstract = fetched
                abstract_source = str(
                    lookup.get("source", "elsevier-api")
                )
        from collectors.metadata_fallback import _is_no_abstract_notice

        no_abstract = _is_no_abstract_notice(abstract)
        if no_abstract:
            abstract = ""
            abstract_source = ""
        flags = ["title_cn_missing", "abstract_cn_missing"]
        if not doi:
            flags.append("doi_missing")
        if not detail.get("authors"):
            flags.append("authors_missing")
        if not abstract:
            flags.append("abstract_en_missing")
        source_url = str(detail.get("source_url", "")) or (
            f"https://www.sciencedirect.com/science/article/pii/{pii}"
            if pii
            else entry["detail_url"]
        )
        return {
            "paper_id": f"doi:{doi}" if doi else f"pii:{pii}",
            "sequence": 0,
            "source_sequence": 0,
            "article_type": (
                "comment" if no_abstract else _article_type(detail.get("title_en", entry["title_en"]))
            ),
            "title_en": detail.get("title_en", entry["title_en"]),
            "title_cn": "",
            "authors": detail.get("authors", []),
            "abstract_en": abstract,
            "abstract_cn": "",
            "doi": doi,
            "source_url": source_url,
            "publication_date": str(target["year"]),
            "sources": {
                "issue": repec_series_url,
                "roster": "repec-serial-page",
                "metadata": "repec-publisher-supplied",
                "abstract_en": abstract_source,
                **({"repec": entry["detail_url"]} if entry.get("detail_url") else {}),
            },
            "translation": {
                "status": "blocked" if not abstract else "pending",
                "provider": "",
                "prompt_version": "",
                "glossary_version": "1",
            },
            "quality_flags": flags,
        }

    built = [build_article(item) for item in target["items"]]
    excluded_items: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []
    for article in built:
        if NON_RESEARCH_PATTERN.search(article["title_en"]):
            excluded_items.append(
                {
                    "title_en": article["title_en"],
                    "reason": "non_research_title",
                    "doi": article["doi"],
                }
            )
            continue
        articles.append(article)
    for sequence, article in enumerate(articles, start=1):
        article["sequence"] = sequence
        article["source_sequence"] = sequence
    if not articles:
        raise ValueError(f"RePEc volume {volume} has no publishable articles")

    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    abstract_complete = sum(
        bool(str(article.get("abstract_en", "")).strip())
        for article in articles
    )
    authors_complete = sum(bool(article.get("authors")) for article in articles)
    flags = ["translation_incomplete"]
    if duplicate_count:
        flags.append("duplicate_doi")
    if abstract_complete != len(articles):
        flags.append("abstract_en_incomplete")
    if authors_complete != len(articles):
        flags.append("authors_incomplete")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    month_dates = [
        str(article.get("publication_date", ""))
        for article in built
        if str(article.get("publication_date", "")).strip()
    ]
    if month_dates:
        from collections import Counter

        publication_date = Counter(month_dates).most_common(1)[0][0]
    else:
        publication_date = str(target["year"])
    return {
        "schema_version": "1.0",
        "issue_id": f"{journal_id}-{volume}-c",
        "journal_id": journal_id,
        "journal_name": journal_name,
        "volume": volume,
        "issue": "C",
        "publication_date": publication_date,
        "source_url": repec_series_url,
        "retrieved_at": now,
        "expected_article_count": len(articles),
        "research_article_count": len(articles),
        "status": "incomplete",
        "articles": articles,
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_transport": "repec-serial-page",
            "roster_authority": "repec-publisher-supplied",
            "roster_match_scope": "repec-volume-items",
            "excluded_item_count": len(excluded_items),
            "excluded_items": excluded_items,
            "doi_complete": sum(bool(article["doi"]) for article in articles),
            "authors_complete": authors_complete,
            "abstract_en_complete": abstract_complete,
            "translation_complete": 0,
            "duplicate_count": duplicate_count,
            "repec_item_count": len(built),
            "flags": flags,
        },
    }


def _repec_citation_date(soup: BeautifulSoup) -> str:
    """Return a 'Month Year' string from RePEc citation meta, or ''."""

    raw = _meta(soup, "date") or _meta(soup, "citation_publication_date")
    raw = raw.strip()
    for pattern in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(raw[:10], pattern)
            break
        except ValueError:
            continue
    else:
        return ""
    if len(raw) >= 7 and raw[:4].isdigit():
        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        try:
            return f"{month_names[parsed.month - 1]} {parsed.year}"
        except IndexError:
            return str(parsed.year)
    return str(parsed.year)


def _parse_official_issue(content: bytes) -> list[dict[str, Any]]:
    soup = BeautifulSoup(content, "html.parser")
    rows: list[dict[str, Any]] = []
    for card in soup.select("li.js-article-list-item, li.article-item"):
        title_node = card.select_one(".js-article-title, .article-content-title")
        if title_node is None:
            continue
        title = _clean(title_node.get_text(" ", strip=True))
        link = title_node.find_parent("a")
        source_url = str(link.get("href") or "") if link else ""
        pii = _source_pii(source_url or str(link.get("id") or "") if link else "")
        authors_node = card.select_one(".js-article__item__authors")
        authors = [
            " ".join(value.split())
            for value in re.split(r"\s*,\s*", _clean(authors_node.get_text(" ", strip=True)) if authors_node else "")
            if value.strip()
        ]
        abstract_node = card.select_one(".js-abstract-body-text, .abstract-body")
        abstract = _clean(abstract_node.get_text(" ", strip=True)) if abstract_node else ""
        abstract = re.sub(r"^Abstract\s*", "", abstract, flags=re.IGNORECASE)
        doi = _normalize_doi(card.get_text(" ", strip=True))
        raw_type_node = card.select_one(".js-article-subtype")
        raw_type = _clean(raw_type_node.get_text(" ", strip=True)) if raw_type_node else ""
        rows.append(
            {
                "pii": pii,
                "title_en": title,
                "authors": authors,
                "abstract_en": abstract,
                "doi": doi,
                "source_url": source_url or _official_article_url(pii, ""),
                "article_type": _article_type(title, raw_type),
            }
        )
    return rows


def _crossref_issue_date(
    session: requests.Session,
    issn: str,
    volume: str,
    year: str,
    issue: str = "",
) -> str:
    # Crossref commonly exposes article online-publication months rather than
    # the official issue month. Prefer the audited issue calendar when one is
    # configured, including the active RePEc-backed ERE collector path.
    if issue:
        from collectors.metadata_fallback import MONTHS_BY_ISSUE

        official_month = MONTHS_BY_ISSUE.get(issn, {}).get(issue, "")
        if official_month:
            return f"{official_month} {year}"
    url = (
        f"https://api.crossref.org/journals/{issn}/works"
        f"?filter=from-pub-date:{year}-01-01,until-pub-date:{year}-12-31&rows=500"
    )
    try:
        payload = _get(session, url).json()
    except (ElsevierCollectorError, ValueError):
        return year
    months: list[int] = []
    for item in payload.get("message", {}).get("items", []):
        if str(item.get("volume") or "") != volume:
            continue
        for key in ("published-print", "published", "issued", "published-online"):
            parts = item.get(key, {}).get("date-parts", [])
            if parts and parts[0] and len(parts[0]) >= 2:
                months.append(int(parts[0][1]))
                break
    if not months:
        return year
    month = Counter(months).most_common(1)[0][0]
    return datetime(int(year), month, 1).strftime("%B %Y")


def _publication_date_within_horizon(
    publication_date: str,
    *,
    lead_months: int,
    today: date | None = None,
) -> bool:
    """Reject known future issues while allowing imprecise year-only metadata."""

    try:
        publication = datetime.strptime(publication_date, "%B %Y").date()
    except ValueError:
        return True
    current = today or datetime.now(timezone.utc).date()
    offset = current.month - 1 + max(0, lead_months)
    horizon_year = current.year + offset // 12
    horizon_month = offset % 12 + 1
    horizon = date(
        horizon_year,
        horizon_month,
        monthrange(horizon_year, horizon_month)[1],
    )
    return publication <= horizon


def fetch_current_issue(
    *,
    journal_id: str,
    journal_name: str,
    issn: str,
    repec_series_url: str,
    issue_url_template: str,
    rss_url: str = "",
    publication_lead_months: int = 1,
    doi_template: str = "",
    max_workers: int = DETAIL_WORKERS,
) -> dict[str, Any]:
    session = _session()
    inventory = _parse_repec_inventory(
        _get(session, repec_series_url).content,
        repec_series_url,
    )
    volume = inventory["volume"]
    issue_number = inventory["issue"]
    if rss_url:
        try:
            from collectors.metadata_fallback import (
                MetadataFallbackError,
                fetch_sciencedirect_rss_issue,
            )

            series_match = re.search(r"/s/([^/]+/[^/.]+)\.html", repec_series_url)
            rss_issue = fetch_sciencedirect_rss_issue(
                journal_id=journal_id,
                journal_name=journal_name,
                issn=issn,
                current_issue_url=issue_url_template,
                issue_url_template=issue_url_template,
                rss_url=rss_url,
                repec_series_code=series_match.group(1) if series_match else "",
                lead_months=publication_lead_months,
                session=session,
            )
            if rss_issue is not None:
                return rss_issue
        except (requests.RequestException, MetadataFallbackError, ValueError):
            # RePEc remains the last-known-good transport when the optional
            # publisher RSS or enrichment APIs are temporarily unavailable.
            pass
    publication_date = _crossref_issue_date(
        session,
        issn,
        volume,
        inventory["year"],
        issue_number,
    )
    if not _publication_date_within_horizon(
        publication_date,
        lead_months=publication_lead_months,
    ):
        raise ElsevierCollectorError(
            "RePEc candidate is outside the configured publication horizon: "
            f"Vol. {volume} ({publication_date})"
        )
    official_issue_url = issue_url_template.format(
        volume=volume,
        issue=issue_number,
        issue_lower=issue_number.lower(),
    )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        details = list(
            pool.map(
                lambda item: _parse_repec_detail(
                    session,
                    item,
                    doi_template=doi_template,
                ),
                inventory["items"],
            )
        )
    detail_by_pii = {item["pii"]: item for item in details if item["pii"]}

    official_rows: list[dict[str, Any]] = []
    official_error = ""
    try:
        official_rows = _parse_official_issue(_get(session, official_issue_url, attempts=3, patient_403=True).content)
    except ElsevierCollectorError as error:
        official_error = str(error)

    source_rows = official_rows or details
    order_override_applied = False
    if not official_rows:
        override_path = ORDER_OVERRIDES / f"{journal_id}-{volume}.json"
        if override_path.exists():
            override = json.loads(override_path.read_text(encoding="utf-8"))
            ordered_pii = override.get("pii_order", [])
            rank = {str(pii).upper(): index for index, pii in enumerate(ordered_pii)}
            research_pii = {
                row.get("pii", "").upper()
                for row in details
                if row.get("pii") and not NON_RESEARCH_PATTERN.search(row.get("title_en", ""))
            }
            if research_pii and research_pii == set(rank):
                source_rows = sorted(
                    details,
                    key=lambda row: rank.get(row.get("pii", "").upper(), len(rank) + details.index(row)),
                )
                order_override_applied = True
    articles: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for source_sequence, raw in enumerate(source_rows, start=1):
        enriched = {**detail_by_pii.get(raw.get("pii", ""), {}), **raw}
        if NON_RESEARCH_PATTERN.search(enriched.get("title_en", "")):
            excluded.append(
                {
                    "title_en": enriched.get("title_en", ""),
                    "reason": "non_research_title",
                    "doi": enriched.get("doi", ""),
                }
            )
            continue
        doi = enriched.get("doi", "")
        authors = enriched.get("authors", [])
        abstract = enriched.get("abstract_en", "")
        flags: list[str] = []
        if not doi:
            flags.append("doi_missing")
        if not authors:
            flags.append("authors_missing")
        if not abstract:
            flags.append("abstract_en_missing")
        flags.extend(["title_cn_missing", "abstract_cn_missing"])
        sequence = len(articles) + 1
        articles.append(
            {
                "paper_id": f"doi:{doi}" if doi else f"pii:{enriched.get('pii', sequence)}",
                "sequence": sequence,
                "source_sequence": source_sequence,
                "article_type": enriched.get("article_type", "research-article"),
                "title_en": enriched.get("title_en", ""),
                "title_cn": "",
                "authors": authors,
                "abstract_en": abstract,
                "abstract_cn": "",
                "doi": doi,
                "source_url": enriched.get("source_url") or _official_article_url(enriched.get("pii", ""), enriched.get("detail_url", "")),
                "publication_date": inventory["year"],
                "sources": {
                    "issue": official_issue_url,
                    "roster": "official-sciencedirect-issue" if official_rows else "repec-publisher-supplied",
                    "metadata": enriched.get("detail_url", "") or "official-sciencedirect-issue",
                    "abstract_en": "official-sciencedirect-issue" if raw.get("abstract_en") else "repec-publisher-supplied",
                },
                "translation": {
                    "status": "pending",
                    "provider": "",
                    "prompt_version": "",
                    "glossary_version": "1",
                },
                "quality_flags": flags,
            }
        )

    count = len(articles)
    duplicate_count = count - len({article["doi"] or article["paper_id"] for article in articles})
    quality_flags = ["translation_incomplete"]
    if not official_rows:
        quality_flags.extend(
            [
                "publisher_html_blocked_repec_fallback",
                "repec_publisher_supplied_roster",
            ]
        )
        if order_override_applied:
            quality_flags.append("official_order_override_applied")
        else:
            quality_flags.append("official_order_unverified")
    required_abstract_count = sum(
        article["article_type"] != "comment" for article in articles
    )
    if (
        sum(bool(article["abstract_en"]) for article in articles)
        < required_abstract_count
    ):
        quality_flags.append("abstract_en_incomplete")
    if sum(bool(article["doi"]) for article in articles) != count:
        quality_flags.append("doi_incomplete")
    if sum(bool(article["authors"]) for article in articles) != count:
        quality_flags.append("authors_incomplete")

    return {
        "schema_version": "1.0",
        "issue_id": f"{journal_id}-{volume}-{issue_number.lower()}",
        "journal_id": journal_id,
        "journal_name": journal_name,
        "volume": volume,
        "issue": issue_number,
        "issue_label": f"Vol. {volume}",
        "publication_date": publication_date,
        "source_url": official_issue_url,
        "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "expected_article_count": count,
        "research_article_count": count,
        "status": "incomplete",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "official_item_count": len(source_rows),
            "excluded_item_count": len(excluded),
            "excluded_items": excluded,
            "doi_complete": sum(bool(article["doi"]) for article in articles),
            "authors_complete": sum(bool(article["authors"]) for article in articles),
            "abstract_en_complete": sum(bool(article["abstract_en"]) for article in articles),
            "translation_complete": 0,
            "duplicate_count": duplicate_count,
            "flags": quality_flags,
            "official_issue_fetch_error": official_error,
        },
    }
