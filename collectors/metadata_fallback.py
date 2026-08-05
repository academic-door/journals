from __future__ import annotations

import html
import os
import re
import time
from calendar import monthrange
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from collectors.article_types import (
    canonical_article_type,
    exclusion_reason,
    is_publishable_type,
)


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
CROSSREF_API = "https://api.crossref.org"
OPENALEX_API = "https://api.openalex.org"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"
ELSEVIER_ARTICLE_METADATA_API = "https://api.elsevier.com/content/metadata/article"
ELSEVIER_SEARCH_API = "https://api.elsevier.com/content/search/sciencedirect"
ELSEVIER_ARTICLE_API = "https://api.elsevier.com/content/article"
ELSEVIER_ABSTRACT_API = "https://api.elsevier.com/content/abstract"
# Elsevier reports per-API quotas in response headers (reset every 7 days).
# Keep a snapshot of every answered request and warn before the cap is hit.
RATE_LIMIT_HEADERS = (
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
)
QUOTA_WARNING_FRACTION = 0.10
NON_RESEARCH_PATTERN = re.compile(
    r"front\s*matter|back\s*matter|editorial\s*board|table\s*of\s*contents|"
    r"recent\s*referees|turnaround\s*times|issue\s+information|"
    r"^announcements?$|american\s+finance\s+association|annual\s+report|"
    r"report\s+of\s+the\s+editor.+for\s+the\s+year|"
    r"summaries\s+of\s+doctoral\s+dissertations|"
    r"abstracts\s+of\s+papers\s+presented|editors?[’'＊]?\s+notes?|"
    r"^\s*editorial(?:\s|:|$)|"
    r"^\s*(?:an\s+)?issue\s+dedicated\s+to\b|"
    r"^\s*special\s+issue\b|"
    r"\bintroduction(?:\s+to\b|\s*:|\s*$)|"
    r"outstanding\s+doctoral\s+dissertation\s+award|"
    r"recommendations?\s+for\s+further\s+reading|"
    r"acknowledg(?:e)?ments?\s+of\s+referees|"
    r"\baddendum\b|\bby\b.+\bpp\.|"
    r"^correction(?:\s+to\b|:|\s*$)|^erratum(?:\s+to\b|:|\s*$)|"
    r"^corrigendum(?:\s+to\b|:|\s*$)|"
    r"submission\s+of\s+manuscripts",
    re.IGNORECASE,
)
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s?&#\"'<>]+", re.IGNORECASE)
COMMENT_PATTERN = re.compile(
    r"\ba\s+comment\b|^comments?(?:\s+on)?\b|^reply(?:\s+to)?\b|:\s*(?:a\s+)?comments?\s*$|:\s*reply\s*$",
    re.IGNORECASE,
)
NO_ABSTRACT_PATTERN = re.compile(
    r"^(?:none|null|n/?a|please provide abstract|"
    r"no abstract(?: is)? available(?: for this item)?)\.?$",
    re.IGNORECASE,
)


def _article_type(title: str) -> str:
    return canonical_article_type(title)


def _is_no_abstract_notice(value: str) -> bool:
    return bool(NO_ABSTRACT_PATTERN.match((value or "").strip()))


def _has_abstract_or_allowed_comment(article: dict[str, Any]) -> bool:
    return bool(article["abstract_en"]) or (
        article["article_type"] == "comment" and not article["abstract_en"]
    )
MONTHS_BY_ISSUE = {
    "0022-3808": {
        str(index): month
        for index, month in enumerate(
            (
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            ),
            start=1,
        )
    },
    "0033-5533": {"1": "February", "2": "May", "3": "August", "4": "November"},
    "0034-6527": {
        "1": "January",
        "2": "March",
        "3": "May",
        "4": "July",
        "5": "September",
        "6": "November",
    },
    # Environmental and Resource Economics (Springer). Crossref records online
    # publication dates, so map official issue months for the 2026 volume.
    "0924-6460": {
        "5": "May",
        "6": "June",
        "7": "July",
        "8": "August",
    },

    "0012-9682": {
        "1": "January",
        "2": "March",
        "3": "May",
        "4": "July",
        "5": "September",
        "6": "November",
    },
}


class MetadataFallbackError(RuntimeError):
    pass


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
    )
    return session


def _get_json(
    session: requests.Session,
    url: str,
    *,
    timeout: int = 60,
    attempts: int = 4,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise MetadataFallbackError(
                    "metadata endpoint returned a non-object"
                )
            return payload
        except (requests.RequestException, ValueError, MetadataFallbackError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise MetadataFallbackError(
        f"metadata endpoint failed after {attempts} attempts: {last_error}"
    )


def _get_content(
    session: requests.Session,
    url: str,
    *,
    timeout: int = 60,
    attempts: int = 4,
    headers: dict[str, str] | None = None,
    patient_403: bool = False,
) -> bytes:
    """GET content with retry; optionally back off patiently on 403.

    ScienceDirect anti-bot blocks are often intermittent. patient_403 waits
    much longer between retries so a later attempt can land in a fresh
    request window, while all other failures keep the short backoff.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            return response.content
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
    raise MetadataFallbackError(
        f"metadata endpoint failed after {attempts} attempts: {last_error}"
    )


def _clean_markup(value: str) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(html.unescape(value), "html.parser")
    cleaned = " ".join(soup.get_text(" ", strip=True).split())
    return "" if _is_no_abstract_notice(cleaned) else cleaned


def _first(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    return str(value or "").strip()


def _number(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else -1


def _page_start(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 10**9


def _locator(item: dict[str, Any]) -> str:
    """Return a stable page/e-locator used to recognize and sort issue items."""

    return str(
        item.get("page")
        or item.get("article-number")
        or item.get("publisher-id")
        or ""
    ).strip()


def _abstract_from_inverted_index(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for token, indexes in value.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int):
                positions.append((index, str(token)))
    positions.sort()
    return " ".join(token for _index, token in positions).strip()


def _openalex_metadata(
    session: requests.Session,
    doi: str,
    *,
    timeout: int,
) -> tuple[list[str], str, str]:
    """Fetch missing author/abstract fields from OpenAlex by DOI."""

    if not doi:
        return [], "", ""
    url = f"{OPENALEX_API}/works/https://doi.org/{doi}"
    try:
        payload = _get_json(session, url, timeout=timeout, attempts=2)
    except MetadataFallbackError:
        return [], "", url
    authors: list[str] = []
    for authorship in payload.get("authorships", []):
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author", {})
        if isinstance(author, dict):
            name = str(author.get("display_name", "")).strip()
            if name:
                authors.append(name)
    abstract = _abstract_from_inverted_index(
        payload.get("abstract_inverted_index")
    )
    return authors, abstract, url


def _semantic_scholar_metadata_batch(
    session: requests.Session,
    dois: list[str],
    *,
    timeout: int,
) -> dict[str, dict[str, Any]]:
    """Fetch missing public metadata in one bounded Semantic Scholar request."""

    normalized = list(dict.fromkeys(doi.strip().lower() for doi in dois if doi.strip()))
    if not normalized or not callable(getattr(session, "post", None)):
        return {}
    url = f"{SEMANTIC_SCHOLAR_API}/paper/batch"
    try:
        response = session.post(
            url,
            params={"fields": "title,authors,abstract,url,externalIds"},
            json={"ids": [f"DOI:{doi}" for doi in normalized]},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return {}
    if not isinstance(payload, list):
        return {}
    results: dict[str, dict[str, Any]] = {}
    for requested_doi, item in zip(normalized, payload):
        if not isinstance(item, dict):
            continue
        external = item.get("externalIds", {})
        returned_doi = (
            str(external.get("DOI", "")).strip().lower()
            if isinstance(external, dict)
            else ""
        )
        doi = returned_doi or requested_doi
        authors = [
            str(author.get("name", "")).strip()
            for author in item.get("authors", [])
            if isinstance(author, dict) and str(author.get("name", "")).strip()
        ]
        results[doi] = {
            "authors": authors,
            "abstract": _clean_markup(str(item.get("abstract", ""))),
            "url": str(item.get("url", "")) or f"{url}/DOI:{doi}",
        }
    return results


def _elsevier_status(status_code: int) -> str:
    return {
        401: "unauthorized",
        403: "insufficient_entitlement",
        404: "not_found",
        429: "quota_exceeded",
    }.get(status_code, "temporary_error" if status_code >= 500 else "request_failed")


def _rate_limit_snapshot(response: object) -> dict[str, Any] | None:
    """Capture Elsevier's per-API quota headers from a response, if present."""

    headers = getattr(response, "headers", {}) or {}
    lowered = {
        str(key).casefold(): str(value).strip()
        for key, value in headers.items()
    }
    raw = {header: lowered.get(header, "") for header in RATE_LIMIT_HEADERS}
    if not any(raw.values()):
        return None
    snapshot: dict[str, Any] = {}
    limit_raw = raw["x-ratelimit-limit"]
    remaining_raw = raw["x-ratelimit-remaining"]
    if limit_raw.isdigit():
        snapshot["limit"] = int(limit_raw)
    elif limit_raw:
        snapshot["limit"] = limit_raw
    if remaining_raw.isdigit():
        snapshot["remaining"] = int(remaining_raw)
    elif remaining_raw:
        snapshot["remaining"] = remaining_raw
    reset_raw = raw["x-ratelimit-reset"]
    if reset_raw.isdigit():
        try:
            snapshot["resets_at"] = datetime.fromtimestamp(
                int(reset_raw), timezone.utc
            ).isoformat()
        except (OSError, OverflowError, ValueError):
            snapshot["reset_epoch"] = reset_raw
    status_raw = lowered.get("x-els-status", "")
    if status_raw:
        snapshot["els_status"] = status_raw
    return snapshot


def _quota_warning(name: str, snapshot: dict[str, Any]) -> str:
    """Return a warning when the remaining weekly quota drops below 10%."""

    remaining = snapshot.get("remaining")
    limit = snapshot.get("limit")
    if isinstance(remaining, int) and isinstance(limit, int) and limit > 0:
        if remaining <= max(1, round(limit * QUOTA_WARNING_FRACTION)):
            return (
                f"{name}: Elsevier API quota nearly exhausted "
                f"({remaining}/{limit} remaining)"
            )
    return ""


# Some Elsevier descriptions append the acknowledgment footnote to the abstract
# with its marker fused into the text (e.g. ``...run is low11The authors are
# grateful to...``). That footnote is not abstract content and its digits trip
# the translation numeric gate, so strip it when it appears in the tail.
ABSTRACT_FOOTNOTE_PATTERNS = (
    r"(?:\d+\s*)?(?:The authors|The author)\s+are\s+(?:grateful|indebted|thankful)\s+to\b",
    r"(?:\d+\s*)?(?:We|I)\s+are\s+(?:grateful|indebted|thankful)\s+to\b",
    r"(?:\d+\s*)?(?:The authors|The author|We|I)\s+thank\b",
    r"(?:\d+\s*)?Financial\s+support\s+from\b",
    r"(?:\d+\s*)?The\s+views\s+expressed\b",
    r"(?:\d+\s*)?Any\s+(?:remaining\s+)?errors\b",
    r"(?:\d+\s*)?Supplementary\s+(?:data|material)\b",
)


def _strip_abstract_footnotes(value: str) -> str:
    """Cut appended acknowledgment/footnote text from the abstract tail."""

    if len(value) < 200:
        return value
    for pattern in ABSTRACT_FOOTNOTE_PATTERNS:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match and match.start() >= len(value) * 0.5:
            stripped = value[: match.start()].strip()
            if len(stripped) >= 100:
                return stripped
    return value


def _elsevier_text(root: ElementTree.Element, names: set[str]) -> str:
    for node in root.iter():
        if _local_name(node.tag) not in names:
            continue
        value = _clean_markup(" ".join(node.itertext()))
        if value and not _is_no_abstract_notice(value):
            return _strip_abstract_footnotes(value)
    return ""


def _elsevier_abstract_link(root: ElementTree.Element) -> str:
    for node in root.iter():
        if _local_name(node.tag) != "link":
            continue
        relation = str(node.attrib.get("ref") or node.attrib.get("rel") or "").lower()
        href = str(node.attrib.get("href") or node.attrib.get("@href") or "").strip()
        if relation != "abstract" or not href:
            continue
        if href.startswith("http://api.elsevier.com/"):
            href = "https://" + href.removeprefix("http://")
        parsed = urlparse(href)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "api.elsevier.com"
            and parsed.path.startswith("/content/abstract/")
        ):
            return href
    return ""


def _elsevier_lookup(
    session: requests.Session,
    pii: str,
    *,
    doi: str = "",
    timeout: int,
) -> dict[str, Any]:
    """Resolve an Elsevier abstract through the documented metadata chain.

    ScienceDirect's own article-information use case points clients to the
    Article Metadata and Search APIs. Scopus also documents that a failed PII
    lookup should fall back to ScienceDirect Search and its returned abstract
    link. Keep snippets separate from full abstracts so a teaser can never pass
    the publication quality gate as if it were the publisher abstract.
    """

    api_key = os.getenv("ELSEVIER_API_KEY", "").strip()
    inst_token = os.getenv("ELSEVIER_INST_TOKEN", "").strip()
    normalized_pii = re.sub(r"[^A-Za-z0-9]", "", pii or "")
    normalized_doi = (doi or "").strip().lower()
    result: dict[str, Any] = {
        "abstract": "",
        "teaser": "",
        "source_url": "",
        "source": "",
        "status": "unconfigured" if not api_key else "not_found",
        "attempts": [],
        "rate_limit": None,
        "quota_warning": "",
    }
    if not api_key or not (normalized_doi or normalized_pii):
        return result
    headers = {
        "Accept": "text/xml,application/xml",
        "X-ELS-APIKey": api_key,
    }
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token

    def request_xml(
        name: str,
        url: str,
        params: dict[str, str],
    ) -> ElementTree.Element | None:
        try:
            response = session.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException:
            result["attempts"].append(
                {"source": name, "status_code": 0, "outcome": "temporary_error"}
            )
            result["status"] = "temporary_error"
            return None
        status_code = int(getattr(response, "status_code", 200))
        snapshot = _rate_limit_snapshot(response)
        if snapshot:
            result["rate_limit"] = snapshot
            warning = _quota_warning(name, snapshot)
            if warning:
                result["quota_warning"] = warning
        attempt: dict[str, Any] = {
            "source": name,
            "status_code": status_code,
            "outcome": _elsevier_status(status_code),
        }
        if snapshot:
            attempt["rate_limit"] = snapshot
        if status_code >= 400:
            result["attempts"].append(attempt)
            result["status"] = _elsevier_status(status_code)
            if status_code == 403 and not inst_token:
                result["status"] = "insufficient_entitlement_missing_insttoken"
            return None
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError:
            attempt["outcome"] = "invalid_response"
            result["attempts"].append(attempt)
            result["status"] = "invalid_response"
            return None
        attempt["outcome"] = "success"
        result["attempts"].append(attempt)
        return root

    def accept_payload(
        root: ElementTree.Element | None,
        *,
        name: str,
        url: str,
    ) -> str:
        if root is None:
            return ""
        abstract = _elsevier_text(root, {"description", "abstract"})
        teaser = _elsevier_text(root, {"teaser"})
        if teaser and not result["teaser"]:
            result["teaser"] = teaser
        if abstract:
            result.update(
                {
                    "abstract": abstract,
                    "source_url": url,
                    "source": name,
                    "status": "success_full_abstract",
                }
            )
        elif teaser:
            result["status"] = "success_teaser_only"
        else:
            result["status"] = "success_no_abstract"
        return _elsevier_abstract_link(root)

    query = (
        f'DOI("{normalized_doi}")'
        if normalized_doi
        else f'PII("{normalized_pii}")'
    )
    metadata_root = request_xml(
        "elsevier-article-metadata",
        ELSEVIER_ARTICLE_METADATA_API,
        {
            "query": query,
            "view": "COMPLETE",
            "httpAccept": "application/xml",
        },
    )
    abstract_link = accept_payload(
        metadata_root,
        name="elsevier-article-metadata",
        url=ELSEVIER_ARTICLE_METADATA_API,
    )
    if result["abstract"]:
        return result

    search_root = request_xml(
        "elsevier-sciencedirect-search",
        ELSEVIER_SEARCH_API,
        {
            "query": query,
            "field": "url,identifier,doi,pii,description,teaser",
            "count": "25",
            "httpAccept": "application/xml",
        },
    )
    search_link = accept_payload(
        search_root,
        name="elsevier-sciencedirect-search",
        url=ELSEVIER_SEARCH_API,
    )
    abstract_link = search_link or abstract_link
    if result["abstract"]:
        return result

    if abstract_link:
        abstract_root = request_xml(
            "elsevier-scopus-abstract-link",
            abstract_link,
            {"view": "META_ABS", "httpAccept": "application/xml"},
        )
        accept_payload(
            abstract_root,
            name="elsevier-scopus-abstract-link",
            url=abstract_link,
        )
        if result["abstract"]:
            return result

    identifiers: list[tuple[str, str]] = []
    if normalized_doi:
        identifiers.append(("doi", normalized_doi))
    if normalized_pii:
        identifiers.append(("pii", normalized_pii))
    for identifier_type, identifier in identifiers:
        url = f"{ELSEVIER_ABSTRACT_API}/{identifier_type}/{identifier}"
        abstract_root = request_xml(
            f"elsevier-scopus-{identifier_type}",
            url,
            {"view": "META_ABS", "httpAccept": "application/xml"},
        )
        accept_payload(
            abstract_root,
            name=f"elsevier-scopus-{identifier_type}",
            url=url,
        )
        if result["abstract"]:
            return result

    article_identifiers = identifiers or []
    for identifier_type, identifier in article_identifiers:
        url = f"{ELSEVIER_ARTICLE_API}/{identifier_type}/{identifier}"
        article_root = request_xml(
            f"elsevier-article-{identifier_type}",
            url,
            {"view": "META_ABS", "httpAccept": "application/xml"},
        )
        accept_payload(
            article_root,
            name=f"elsevier-article-{identifier_type}",
            url=url,
        )
        if result["abstract"]:
            return result
    return result


def _elsevier_abstract(
    session: requests.Session,
    pii: str,
    *,
    doi: str = "",
    timeout: int,
) -> tuple[str, str]:
    """Backward-compatible tuple wrapper for tests and existing callers."""

    lookup = _elsevier_lookup(session, pii, doi=doi, timeout=timeout)
    return str(lookup["abstract"]), str(lookup["source_url"])


def _is_elsevier_identifier(pii: str, doi: str) -> bool:
    """Avoid sending non-Elsevier works through Elsevier entitlement APIs."""

    normalized_pii = re.sub(r"[^A-Za-z0-9]", "", pii or "")
    normalized_doi = (doi or "").strip().lower()
    return bool(normalized_pii) or normalized_doi.startswith("10.1016/")


def _defer_elsevier_entitlement(previous: dict[str, Any]) -> bool:
    """Do not repeat known entitlement failures until an InstToken appears."""

    if os.getenv("ELSEVIER_INST_TOKEN", "").strip():
        return False
    status = str(
        previous.get("sources", {}).get("abstract_lookup", {}).get("status", "")
    )
    return status in {
        "insufficient_entitlement",
        "insufficient_entitlement_missing_insttoken",
    }


def _missing_insttoken(articles: list[dict[str, Any]]) -> bool:
    return any(
        article.get("sources", {})
        .get("abstract_lookup", {})
        .get("status")
        == "insufficient_entitlement_missing_insttoken"
        for article in articles
    )


def _date_year(item: dict[str, Any]) -> str:
    for key in ("published-print", "published", "issued", "published-online"):
        parts = item.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            return str(parts[0][0])
    return ""


def _publication_date(issn: str, volume: str, issue: str, items: list[dict]) -> str:
    year = next((_date_year(item) for item in items if _date_year(item)), "")
    month = MONTHS_BY_ISSUE.get(issn, {}).get(issue, "")
    if month and year:
        return f"{month} {year}"
    dated_months: list[int] = []
    for item in items:
        for key in ("published-print", "published", "issued", "published-online"):
            parts = item.get(key, {}).get("date-parts", [])
            if parts and parts[0] and len(parts[0]) >= 2:
                dated_months.append(int(parts[0][1]))
                break
    if dated_months and year:
        month_number = Counter(dated_months).most_common(1)[0][0]
        return datetime(int(year), month_number, 1).strftime("%B %Y")
    return year


def _issue_is_not_future(
    issn: str,
    volume: str,
    issue: str,
    items: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> bool:
    del volume
    current = now or datetime.now(timezone.utc)
    years = [
        int(year)
        for item in items
        if (year := _date_year(item)).isdigit()
    ]
    if not years:
        return False
    issue_year = min(years)
    month_name = MONTHS_BY_ISSUE.get(issn, {}).get(issue, "")
    if month_name:
        issue_month = datetime.strptime(month_name, "%B").month
        return (issue_year, issue_month) <= (current.year, current.month)
    date_parts: list[tuple[int, int, int]] = []
    for item in items:
        for key in ("published-print", "published", "issued", "published-online"):
            parts = item.get(key, {}).get("date-parts", [])
            if not parts or not parts[0]:
                continue
            values = list(parts[0]) + [1, 1]
            date_parts.append((int(values[0]), int(values[1]), int(values[2])))
            break
    return bool(date_parts) and min(date_parts) <= (
        current.year,
        current.month,
        current.day,
    )


def _authors(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for author in item.get("author", []):
        name = " ".join(
            part for part in (author.get("given", ""), author.get("family", "")) if part
        ).strip()
        if name:
            names.append(name)
    return names


def _repec_doi_url(doi: str, series_code: str = "ucp/jpolec") -> str:
    return (
        f"https://ideas.repec.org/a/{series_code.strip('/')}/"
        f"doi{doi.replace('/', '-')}.html"
    )


def _repec_serial_url(series_code: str = "ucp/jpolec") -> str:
    return f"https://ideas.repec.org/s/{series_code.strip('/')}.html"


def _extract_doi(value: str) -> str:
    decoded = html.unescape(value or "")
    match = DOI_PATTERN.search(decoded.replace("-", "/", 1) if decoded.startswith("doi10.") else decoded)
    if match:
        return match.group(0).rstrip(".,;:)]}").lower()
    match = re.search(
        r"/doi(10\.\d{4,9})-([^/]+)\.html(?:$|\?)",
        decoded,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1)}/{match.group(2)}".rstrip(".,;:)]}").lower()
    return ""


def _parse_repec_serial_issues(
    content: bytes | str,
    serial_url: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(content, "html.parser")
    issues: list[dict[str, Any]] = []
    heading_pattern = re.compile(
        r"(?P<year>\d{4}),\s*Volume\s+(?P<volume>[A-Za-z0-9.-]+),\s*"
        r"Issue\s+(?P<issue>[A-Za-z0-9.-]+)",
        re.IGNORECASE,
    )
    for heading in soup.find_all(["h2", "h3"]):
        match = heading_pattern.search(heading.get_text(" ", strip=True))
        if not match:
            continue
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) in {"h2", "h3"}:
                break
            if not hasattr(sibling, "select"):
                continue
            for link in sibling.select("a[href^='/a/']"):
                detail_url = urljoin(serial_url, str(link.get("href") or ""))
                title = _clean_markup(link.get_text(" ", strip=True))
                if not title or detail_url in seen:
                    continue
                seen.add(detail_url)
                items.append(
                    {
                        "title": title,
                        "detail_url": detail_url,
                        "doi": _extract_doi(detail_url),
                    }
                )
        if items:
            issues.append(
                {
                    "year": match.group("year"),
                    "volume": match.group("volume"),
                    "issue": match.group("issue"),
                    "items": items,
                }
            )
    return issues


def _repec_abstract(
    session: requests.Session,
    doi: str,
    *,
    timeout: int,
    series_code: str = "ucp/jpolec",
) -> tuple[str, str]:
    url = _repec_doi_url(doi, series_code)
    response = session.get(
        url,
        timeout=timeout,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    heading = next(
        (
            node
            for node in soup.find_all(["h2", "h3"])
            if node.get_text(" ", strip=True).lower() == "abstract"
        ),
        None,
    )
    if heading is None:
        return "", url
    chunks: list[str] = []
    for sibling in heading.next_siblings:
        name = getattr(sibling, "name", None)
        if name in {"h2", "h3"}:
            break
        if hasattr(sibling, "get_text"):
            text = sibling.get_text(" ", strip=True)
        else:
            text = str(sibling).strip()
        if text:
            chunks.append(text)
    return " ".join(chunks).strip(), url


def _repec_detail_metadata(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
) -> tuple[str, list[str], str]:
    response = session.get(
        url,
        timeout=timeout,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    doi = _extract_doi(soup.get_text(" ", strip=True))

    authors: list[str] = []
    author_heading = next(
        (
            node
            for node in soup.find_all(["h2", "h3"])
            if node.get_text(" ", strip=True).casefold() in {"author", "authors"}
        ),
        None,
    )
    if author_heading is not None:
        for sibling in author_heading.next_siblings:
            if getattr(sibling, "name", None) in {"h2", "h3"}:
                break
            if not hasattr(sibling, "select"):
                continue
            for item in sibling.select("li"):
                name = _clean_markup(item.get_text(" ", strip=True))
                if name and name not in authors:
                    authors.append(name)

    abstract = ""
    abstract_heading = next(
        (
            node
            for node in soup.find_all(["h2", "h3"])
            if node.get_text(" ", strip=True).casefold() == "abstract"
        ),
        None,
    )
    if abstract_heading is not None:
        chunks: list[str] = []
        for sibling in abstract_heading.next_siblings:
            if getattr(sibling, "name", None) in {"h2", "h3"}:
                break
            text = (
                sibling.get_text(" ", strip=True)
                if hasattr(sibling, "get_text")
                else str(sibling).strip()
            )
            if text:
                chunks.append(text)
        abstract = " ".join(chunks).strip()
    return doi, authors, abstract


def fetch_repec_history_issue(
    *,
    journal_id: str,
    journal_name: str,
    issn: str,
    volume: str,
    issue: str,
    repec_series_code: str = "ucp/jpolec",
    session: requests.Session | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Build one historical JPE issue from the public RePEc serial page."""

    client = session or _session()
    serial_url = _repec_serial_url(repec_series_code)
    response = client.get(
        serial_url,
        timeout=timeout,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    response.raise_for_status()
    issues = _parse_repec_serial_issues(response.content, serial_url)
    target = next(
        (
            record
            for record in issues
            if str(record["volume"]) == str(volume) and str(record["issue"]) == str(issue)
        ),
        None,
    )
    if target is None:
        raise MetadataFallbackError(
            f"RePEc serial page has no issue {volume}/{issue}"
        )

    crossref_items = _crossref_items(issn, session=client, timeout=timeout)
    crossref_by_doi = {
        str(item.get("DOI", "")).strip().lower(): item
        for item in crossref_items
        if str(item.get("volume", "")).strip() == str(volume)
        and str(item.get("issue", "")).strip() == str(issue)
        and str(item.get("DOI", "")).strip()
    }

    excluded_items: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []

    def build_article(entry: dict[str, str]) -> dict[str, Any]:
        title = entry["title"]
        doi = entry["doi"]
        detail_authors: list[str] = []
        detail_abstract = ""
        if entry.get("detail_url") and not doi:
            try:
                doi, detail_authors, detail_abstract = _repec_detail_metadata(
                    client,
                    entry["detail_url"],
                    timeout=timeout,
                )
            except requests.RequestException:
                doi = ""
        crossref = crossref_by_doi.get(doi, {})
        authors = _authors(crossref) or detail_authors
        abstract = (
            _clean_markup(str(crossref.get("abstract", ""))) or detail_abstract
        )
        abstract_source = (
            "crossref"
            if crossref.get("abstract")
            else ("repec-publisher-supplied" if detail_abstract else "")
        )
        repec_url = ""
        if not abstract and doi:
            try:
                abstract, repec_url = _repec_abstract(
                    client,
                    doi,
                    timeout=timeout,
                    series_code=repec_series_code,
                )
            except requests.RequestException:
                abstract = ""
            if abstract:
                abstract_source = "repec-publisher-supplied"
        no_abstract_notice = _is_no_abstract_notice(abstract)
        if no_abstract_notice:
            abstract = ""
            abstract_source = ""
        openalex_url = ""
        if doi and (not authors or not abstract):
            openalex_authors, openalex_abstract, openalex_url = _openalex_metadata(
                client, doi, timeout=timeout
            )
            if not authors and openalex_authors:
                authors = openalex_authors
            if not abstract and openalex_abstract:
                abstract = openalex_abstract
                abstract_source = "openalex"
        flags = ["title_cn_missing", "abstract_cn_missing"]
        if not doi:
            flags.append("doi_missing")
        if not authors:
            flags.append("authors_missing")
        if not abstract:
            flags.append("abstract_en_missing")
        return {
            "paper_id": f"doi:{doi}" if doi else f"{journal_id}:{title}",
            "sequence": 0,
            "source_sequence": 0,
            "article_type": "comment" if no_abstract_notice else _article_type(title),
            "title_en": title,
            "title_cn": "",
            "authors": authors,
            "abstract_en": abstract,
            "abstract_cn": "",
            "doi": doi,
            "source_url": f"https://doi.org/{doi}" if doi else entry["detail_url"],
            "publication_date": target["year"],
            "sources": {
                "issue": serial_url,
                "roster": "repec-serial-page",
                "metadata": "crossref",
                "abstract_en": abstract_source,
                **({"repec": repec_url or entry["detail_url"]} if repec_url or entry.get("detail_url") else {}),
                **({"openalex": openalex_url} if openalex_url else {}),
            },
            "translation": {
                "status": "blocked" if not abstract else "pending",
                "provider": "",
                "prompt_version": "",
                "glossary_version": "1",
            },
            "quality_flags": flags,
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        built_items = list(pool.map(build_article, target["items"]))

    for article in built_items:
        if NON_RESEARCH_PATTERN.search(article["title_en"]):
            excluded_items.append(
                {
                    "title_en": article["title_en"],
                    "reason": "non_research_title",
                    "doi": article["doi"],
                }
            )
            continue
        article["sequence"] = len(articles) + 1
        article["source_sequence"] = article["sequence"]
        articles.append(article)

    if not articles:
        raise MetadataFallbackError(f"RePEc issue {volume}/{issue} has no articles")

    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    abstract_complete = sum(_has_abstract_or_allowed_comment(article) for article in articles)
    authors_complete = sum(bool(article["authors"]) for article in articles)
    flags = ["translation_incomplete"]
    if duplicate_count:
        flags.append("duplicate_doi")
    if abstract_complete != len(articles):
        flags.append("abstract_en_incomplete")
    if _missing_insttoken(articles):
        flags.append("elsevier_insttoken_required")
    if authors_complete != len(articles):
        flags.append("authors_incomplete")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    publication_date = (
        _publication_date(issn, str(volume), str(issue), list(crossref_by_doi.values()))
        or (
            f"{MONTHS_BY_ISSUE.get(issn, {}).get(str(issue), '')} {target['year']}"
        ).strip()
        or target["year"]
    )
    return {
        "schema_version": "1.0",
        "issue_id": f"{journal_id}-{volume}-{issue}",
        "journal_id": journal_id,
        "journal_name": journal_name,
        "volume": str(volume),
        "issue": str(issue),
        "publication_date": publication_date,
        "source_url": serial_url,
        "retrieved_at": now,
        "expected_article_count": len(articles),
        "research_article_count": len(articles),
        "status": "incomplete",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_transport": "repec-serial-page",
            "roster_authority": "repec-publisher-supplied",
            "roster_match_scope": "repec-issue-section",
            "publisher_page_status": "blocked",
            "excluded_item_count": len(excluded_items),
            "excluded_items": excluded_items,
            "doi_complete": sum(bool(article["doi"]) for article in articles),
            "authors_complete": authors_complete,
            "abstract_en_complete": abstract_complete,
            "translation_complete": 0,
            "duplicate_count": duplicate_count,
            "flags": flags,
        },
    }


def _crossref_items(
    issn: str,
    *,
    session: requests.Session,
    timeout: int,
    start_year: int | None = None,
) -> list[dict[str, Any]]:
    start_year = start_year or datetime.now(timezone.utc).year - 1
    url = (
        f"{CROSSREF_API}/journals/{issn}/works"
        f"?filter=from-pub-date:{start_year}-01-01&rows=500"
    )
    payload = _get_json(session, url, timeout=timeout)
    items = payload.get("message", {}).get("items", [])
    if not isinstance(items, list):
        raise MetadataFallbackError("Crossref response has no item list")
    return [item for item in items if isinstance(item, dict)]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _xml_value(node: ElementTree.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in node.iter():
        if _local_name(child.tag) in wanted and child.text:
            value = " ".join(child.text.split())
            if value:
                return value
    return ""


def _doi_from_rss(node: ElementTree.Element) -> str:
    for key in ("doi", "identifier"):
        value = _xml_value(node, key)
        match = re.search(r"10\.\d{4,9}/[^\s?&#]+", value, flags=re.IGNORECASE)
        if match:
            return match.group(0).rstrip(".,;").lower()
    link = _xml_value(node, "link")
    match = re.search(r"10\.\d{4,9}/[^\s?&#]+", link, flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;").lower() if match else ""


def _issue_key(volume: str, issue: str) -> tuple[int, int]:
    return _number(volume), _number(issue)


def _month_horizon(current: date, lead_months: int) -> date:
    offset = current.month - 1 + max(0, lead_months)
    year = current.year + offset // 12
    month = offset % 12 + 1
    return date(year, month, monthrange(year, month)[1])


def _sciencedirect_rss_groups(
    content: bytes,
    *,
    lead_months: int,
    today: date | None = None,
) -> list[tuple[tuple[str, str], list[dict[str, Any]]]]:
    """Return eligible ScienceDirect RSS volumes through the configured horizon."""

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise MetadataFallbackError("ScienceDirect RSS is not valid XML") from error

    cutoff = _month_horizon(today or datetime.now(timezone.utc).date(), lead_months)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for node in root.iter():
        if _local_name(node.tag) != "item":
            continue
        title = _clean_markup(_xml_value(node, "title"))
        description = _clean_markup(_xml_value(node, "description", "encoded"))
        link = _xml_value(node, "link")
        volume_match = re.search(r"\bVolume\s+([A-Za-z0-9.-]+)", description, re.IGNORECASE)
        date_match = re.search(
            r"\bPublication date:\s*([A-Za-z]+\s+\d{4})",
            description,
            re.IGNORECASE,
        )
        if not title or not volume_match or not date_match:
            continue
        try:
            publication = datetime.strptime(
                date_match.group(1).title(), "%B %Y"
            ).date()
        except ValueError:
            continue
        if publication > cutoff:
            continue
        authors_match = re.search(r"\bAuthor\(s\):\s*(.+)$", description, re.IGNORECASE)
        authors = [
            name.strip()
            for name in re.split(r"\s*,\s*", authors_match.group(1) if authors_match else "")
            if name.strip()
        ]
        pii_match = re.search(r"/pii/([A-Za-z0-9]+)", link, re.IGNORECASE)
        key = (volume_match.group(1), publication.strftime("%B %Y"))
        grouped.setdefault(key, []).append(
            {
                "title": title,
                "link": link,
                "doi": _doi_from_rss(node),
                "pii": pii_match.group(1).upper() if pii_match else "",
                "authors": authors,
            }
        )
    return sorted(
        grouped.items(),
        key=lambda group: (
            datetime.strptime(group[0][1], "%B %Y"),
            _number(group[0][0]),
        ),
    )


def _crossref_pii(item: dict[str, Any]) -> str:
    values = [
        str(item.get("resource", {}).get("primary", {}).get("URL", "")),
        *(str(link.get("URL", "")) for link in item.get("link", []) if isinstance(link, dict)),
    ]
    for value in values:
        match = re.search(r"(?:/pii/|PII:)([A-Za-z0-9]+)", value, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return ""


def _normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _official_issue_abstracts(content: bytes) -> dict[str, str]:
    """Extract PII -> abstract from a ScienceDirect official issue page."""

    soup = BeautifulSoup(content, "html.parser")
    abstracts: dict[str, str] = {}
    for card in soup.select("li.js-article-list-item, li.article-item"):
        title_node = card.select_one(".js-article-title, .article-content-title")
        if title_node is None:
            continue
        link = title_node.find_parent("a")
        source_url = str(link.get("href") or "") if link else ""
        pii_match = re.search(r"/pii/([A-Za-z0-9]+)", source_url, re.IGNORECASE)
        if not pii_match:
            continue
        abstract_node = card.select_one(".js-abstract-body-text, .abstract-body")
        abstract = _clean_markup(
            abstract_node.get_text(" ", strip=True) if abstract_node else ""
        )
        if abstract:
            abstracts[pii_match.group(1).upper()] = abstract
    return abstracts


def fetch_sciencedirect_rss_issue(
    *,
    journal_id: str,
    journal_name: str,
    issn: str,
    current_issue_url: str,
    issue_url_template: str,
    rss_url: str,
    repec_series_code: str = "",
    lead_months: int = 1,
    newer_than_volume: str = "",
    session: requests.Session | None = None,
    timeout: int = 60,
    today: date | None = None,
    existing_issue: dict[str, Any] | None = None,
    force_elsevier: bool = False,
) -> dict[str, Any] | None:
    """Build the latest eligible Elsevier issue from the official RSS roster.

    The publisher feed is the sole authority for issue membership and order.
    Crossref, Elsevier, RePEc and OpenAlex only enrich fields on those rows.
    """

    client = session or _session()
    content = _get_content(
        client,
        rss_url,
        timeout=timeout,
        headers={"Accept": "application/rss+xml,application/xml,text/xml"},
    )
    groups = _sciencedirect_rss_groups(
        content,
        lead_months=lead_months,
        today=today,
    )
    if not groups:
        return None
    (volume, publication_date), rss_items = groups[-1]
    # ScienceDirect RSS lists the newest-added article first, while official
    # issue pages and new-issue emails present the issue from first to last.
    rss_items = list(reversed(rss_items))
    if newer_than_volume and _number(volume) <= _number(newer_than_volume):
        return None

    crossref_items = _crossref_items(
        issn,
        session=client,
        timeout=timeout,
        start_year=(today or datetime.now(timezone.utc).date()).year,
    )
    issue_url = issue_url_template.format(
        volume=volume,
        issue="C",
        issue_lower="c",
    )
    rss_research: list[dict[str, Any]] = []
    excluded_items: list[dict[str, Any]] = []
    for item in rss_items:
        item_type = canonical_article_type(str(item.get("title", "")))
        if is_publishable_type(item_type):
            rss_research.append(item)
            continue
        excluded_items.append(
            {
                "title_en": str(item.get("title", "")),
                "article_type": item_type,
                "reason": exclusion_reason(item_type),
                "doi": str(item.get("doi", "")),
                "source_url": str(item.get("link", "")),
            }
        )
    if not rss_research:
        raise MetadataFallbackError("ScienceDirect RSS current volume has no research items")

    crossref_by_doi: dict[str, dict[str, Any]] = {}
    crossref_by_pii: dict[str, dict[str, Any]] = {}
    crossref_by_title: dict[str, dict[str, Any]] = {}
    for item in crossref_items:
        if item.get("type") != "journal-article":
            continue
        item_doi = str(item.get("DOI", "")).strip().lower()
        item_pii = _crossref_pii(item)
        item_title = _normalized_title(_first(item.get("title")))
        if item_doi:
            crossref_by_doi[item_doi] = item
        if item_pii:
            crossref_by_pii[item_pii] = item
        if item_title:
            crossref_by_title[item_title] = item


    issue_id = f"{journal_id}-{volume}-c"
    existing_articles: dict[str, dict[str, Any]] = {}
    if existing_issue and existing_issue.get("issue_id") == issue_id:
        for article in existing_issue.get("articles", []):
            keys = {
                str(article.get("doi", "")).strip().lower(),
                _normalized_title(str(article.get("title_en", ""))),
            }
            pii_match = re.search(
                r"/pii/([A-Za-z0-9]+)",
                str(article.get("source_url", "")),
                re.IGNORECASE,
            )
            if pii_match:
                keys.add(pii_match.group(1).upper())
            for key in keys:
                if key:
                    existing_articles[key] = article

    articles: list[dict[str, Any]] = []
    for sequence, rss_item in enumerate(rss_research, start=1):
        rss_doi = str(rss_item.get("doi", "")).strip().lower()
        rss_pii = str(rss_item.get("pii", "")).strip().upper()
        title_key = _normalized_title(str(rss_item.get("title", "")))
        crossref = (
            crossref_by_doi.get(rss_doi)
            or crossref_by_pii.get(rss_pii)
            or crossref_by_title.get(title_key)
            or {}
        )
        doi = rss_doi or str(crossref.get("DOI", "")).strip().lower()
        crossref_pii = _crossref_pii(crossref)
        pii = rss_pii or crossref_pii
        previous = (
            existing_articles.get(doi)
            or existing_articles.get(pii)
            or existing_articles.get(title_key)
            or {}
        )
        title = str(rss_item["title"])
        authors = list(rss_item.get("authors", [])) or _authors(crossref)
        if not authors:
            authors = list(previous.get("authors", []))
        abstract = _clean_markup(str(crossref.get("abstract", "")))
        abstract_source = "crossref" if abstract else ""
        if not abstract:
            abstract = _clean_markup(str(previous.get("abstract_en", "")))
            abstract_source = str(
                previous.get("sources", {}).get("abstract_en", "")
            ) if abstract else ""

        elsevier_lookup: dict[str, Any] = {}
        abstract_snippet = ""
        if (force_elsevier or not abstract) and _is_elsevier_identifier(
            pii, doi
        ):
            if _defer_elsevier_entitlement(previous):
                elsevier_lookup = {
                    "status": "insufficient_entitlement_missing_insttoken",
                    "attempts": [],
                    "deferred": True,
                }
            else:
                elsevier_lookup = _elsevier_lookup(
                    client,
                    pii,
                    doi=doi,
                    timeout=timeout,
                )
            lookup_abstract = _clean_markup(
                str(elsevier_lookup.get("abstract", ""))
            )
            abstract_snippet = _clean_markup(
                str(elsevier_lookup.get("teaser", ""))
            )
            if lookup_abstract and (force_elsevier or not abstract):
                # A forced run may replace a fallback abstract with the
                # publisher one, but must never downgrade to a teaser or
                # wipe an existing abstract on a failed lookup.
                abstract = lookup_abstract
                abstract_source = str(
                    elsevier_lookup.get("source", "elsevier-api")
                )

        repec_url = ""
        if not abstract and repec_series_code and doi:
            try:
                abstract, repec_url = _repec_abstract(
                    client,
                    doi,
                    timeout=timeout,
                    series_code=repec_series_code,
                )
            except requests.RequestException:
                abstract = ""
            if abstract:
                abstract_source = "repec-publisher-supplied"

        openalex_url = ""
        if doi and (not authors or not abstract):
            openalex_authors, openalex_abstract, openalex_url = _openalex_metadata(
                client,
                doi,
                timeout=timeout,
            )
            if not authors and openalex_authors:
                authors = openalex_authors
            if not abstract and openalex_abstract:
                abstract = openalex_abstract
                abstract_source = "openalex"


        flags = ["title_cn_missing", "abstract_cn_missing"]
        if not doi:
            flags.append("doi_missing")
        if not authors:
            flags.append("authors_missing")
        if not abstract:
            flags.append("abstract_en_missing")
            if abstract_snippet:
                flags.append("abstract_teaser_only")

        sources: dict[str, Any] = {
            "issue": issue_url,
            "roster": rss_url,
            "metadata": "crossref" if crossref else "publisher-rss",
            "abstract_en": abstract_source,
        }
        if elsevier_lookup:
            sources["abstract_lookup"] = {
                "status": elsevier_lookup.get("status", ""),
                "attempts": elsevier_lookup.get("attempts", []),
                **(
                    {"rate_limit": elsevier_lookup["rate_limit"]}
                    if elsevier_lookup.get("rate_limit")
                    else {}
                ),
                **(
                    {"quota_warning": elsevier_lookup["quota_warning"]}
                    if elsevier_lookup.get("quota_warning")
                    else {}
                ),
            }
            if elsevier_lookup.get("source_url"):
                sources["elsevier"] = elsevier_lookup["source_url"]
        if repec_url:
            sources["repec"] = repec_url
        if openalex_url:
            sources["openalex"] = openalex_url

        article = {
            "paper_id": (
                f"doi:{doi}"
                if doi
                else f"pii:{pii}" if pii else f"{journal_id}:{volume}:{sequence}"
            ),
            "sequence": sequence,
            "source_sequence": sequence,
            "article_type": _article_type(title),
            "title_en": title,
            "title_cn": str(previous.get("title_cn", "")),
            "authors": authors,
            "abstract_en": abstract,
            "abstract_cn": str(previous.get("abstract_cn", "")),
            "abstract_snippet_en": abstract_snippet,
            "doi": doi,
            "source_url": str(rss_item.get("link", ""))
            or (f"https://doi.org/{doi}" if doi else issue_url),
            "publication_date": str(previous.get("publication_date", ""))
            or publication_date,
            "sources": sources,
            "translation": dict(
                previous.get(
                    "translation",
                    {
                        "status": "blocked" if not abstract else "pending",
                        "provider": "",
                        "prompt_version": "",
                        "glossary_version": "1",
                    },
                )
            ),
            "quality_flags": flags,
        }
        if article["title_cn"]:
            article["quality_flags"].remove("title_cn_missing")
        if article["abstract_cn"]:
            article["quality_flags"].remove("abstract_cn_missing")
        articles.append(article)

    if any(
        not article.get("abstract_en") for article in articles
    ):
        try:
            official_html = _get_content(
                client,
                issue_url,
                timeout=timeout,
                attempts=2,
                patient_403=True,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
        except MetadataFallbackError:
            official_html = b""
        official_abstracts = _official_issue_abstracts(official_html)
        if official_abstracts:
            for article in articles:
                if article.get("abstract_en"):
                    continue
                pii_match = re.search(
                    r"/pii/([A-Za-z0-9]+)",
                    str(article.get("source_url", "")),
                    re.IGNORECASE,
                )
                if not pii_match:
                    continue
                abstract = official_abstracts.get(pii_match.group(1).upper(), "")
                if abstract:
                    article["abstract_en"] = abstract
                    article["sources"]["abstract_en"] = "official-sciencedirect-issue"
                    article["quality_flags"] = [
                        flag
                        for flag in article["quality_flags"]
                        if flag != "abstract_en_missing"
                    ]
                    if article.get("translation", {}).get("status") == "blocked":
                        article["translation"]["status"] = "pending"

    unresolved_dois = [
        str(article.get("doi", "")).strip().lower()
        for article in articles
        if article.get("doi")
        and (not article.get("authors") or not article.get("abstract_en"))
    ]
    semantic_by_doi = (
        _semantic_scholar_metadata_batch(
            client, unresolved_dois, timeout=min(timeout, 5)
        )
        if unresolved_dois
        else {}
    )
    for article in articles:
        semantic = semantic_by_doi.get(str(article.get("doi", "")).lower(), {})
        if not semantic:
            continue
        semantic_url = str(semantic.get("url", ""))
        if not article.get("authors") and semantic.get("authors"):
            article["authors"] = list(semantic["authors"])
            article["quality_flags"] = [
                flag for flag in article["quality_flags"] if flag != "authors_missing"
            ]
        if not article.get("abstract_en") and semantic.get("abstract"):
            article["abstract_en"] = str(semantic["abstract"])
            article["sources"]["abstract_en"] = "semantic-scholar"
            article["quality_flags"] = [
                flag for flag in article["quality_flags"] if flag != "abstract_en_missing"
            ]
            if article.get("translation", {}).get("status") == "blocked":
                article["translation"]["status"] = "pending"
        if semantic_url:
            article["sources"]["semantic_scholar"] = semantic_url

    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    abstract_complete = sum(
        _has_abstract_or_allowed_comment(article) for article in articles
    )
    flags = [
        "publisher_html_blocked_sciencedirect_rss_fallback",
        "publisher_rss_reverse_order_normalized",
        "official_order_unverified",
    ]
    if abstract_complete != len(articles):
        flags.append("abstract_en_incomplete")
    if _missing_insttoken(articles):
        flags.append("elsevier_insttoken_required")
    if duplicate_count:
        flags.append("duplicate_doi")
    translation_complete = sum(
        bool(article["title_cn"])
        and (
            bool(article["abstract_cn"])
            or article.get("article_type") == "comment"
        )
        for article in articles
    )
    if translation_complete != len(articles):
        flags.append("translation_incomplete")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": journal_id,
        "journal_name": journal_name,
        "volume": volume,
        "issue": "C",
        "issue_label": f"Vol. {volume}",
        "publication_date": publication_date,
        "source_url": issue_url,
        "retrieved_at": now,
        "expected_article_count": len(articles),
        "research_article_count": len(articles),
        "status": "incomplete" if flags else "ready",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_transport": "sciencedirect-rss",
            "roster_authority": "publisher-rss",
            "roster_match_scope": "rss-volume-items",
            "rss_url": rss_url,
            "official_item_count": len(rss_items),
            "publishable_item_count": len(articles),
            "excluded_item_count": len(excluded_items),
            "excluded_items": excluded_items,
            "doi_complete": sum(bool(article["doi"]) for article in articles),
            "authors_complete": sum(bool(article["authors"]) for article in articles),
            "abstract_en_complete": abstract_complete,
            "translation_complete": translation_complete,
            "duplicate_count": duplicate_count,
            "flags": flags,
        },
    }


def fetch_official_rss_issue(
    *,
    journal_id: str,
    journal_name: str,
    issn: str,
    current_issue_url: str,
    rss_url: str,
    repec_jpe: bool = False,
    repec_series_code: str = "",
    session: requests.Session | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Use a publisher's public current-issue RSS as roster authority.

    Crossref enriches authors and deposited abstracts by DOI. If the publisher
    RSS visibly lags a newer, populated Crossref issue, the function returns a
    clearly flagged Crossref snapshot instead of silently presenting the stale
    feed as current.
    """

    client = session or _session()
    response = client.get(
        rss_url,
        timeout=timeout,
        headers={"Accept": "application/rss+xml,application/xml,text/xml"},
    )
    response.raise_for_status()
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as error:
        raise MetadataFallbackError("publisher RSS is not valid XML") from error

    rss_items: list[dict[str, Any]] = []
    for node in root.iter():
        if _local_name(node.tag) != "item":
            continue
        volume = _xml_value(node, "volume")
        issue = _xml_value(node, "number", "issue")
        if not volume or not issue:
            continue
        rss_items.append(
            {
                "volume": volume,
                "issue": issue,
                "title": _clean_markup(_xml_value(node, "title")),
                "link": _xml_value(node, "link"),
                "doi": _doi_from_rss(node),
                "page_start": _xml_value(node, "startingpage"),
                "page_end": _xml_value(node, "endingpage"),
                "cover_date": _xml_value(node, "coverdate", "date"),
                "description": _clean_markup(
                    _xml_value(node, "description", "encoded")
                ),
            }
        )
    if not rss_items:
        raise MetadataFallbackError("publisher RSS contains no issue items")
    rss_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in rss_items:
        rss_groups.setdefault((item["volume"], item["issue"]), []).append(item)
    (volume, issue), current_items = max(
        rss_groups.items(),
        key=lambda group: _issue_key(*group[0]),
    )

    crossref_items = _crossref_items(issn, session=client, timeout=timeout)
    crossref_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    crossref_by_doi: dict[str, dict[str, Any]] = {}
    for item in crossref_items:
        item_volume = str(item.get("volume", "")).strip()
        item_issue = str(item.get("issue", "")).strip()
        doi = str(item.get("DOI", "")).strip().lower()
        if doi:
            crossref_by_doi[doi] = item
        if item_volume and item_issue and item.get("type") == "journal-article":
            crossref_groups.setdefault((item_volume, item_issue), []).append(item)
    eligible_crossref = [
        (key, items)
        for key, items in crossref_groups.items()
        if _issue_is_not_future(issn, key[0], key[1], items)
        if sum(
            bool(item.get("DOI"))
            and not NON_RESEARCH_PATTERN.search(_first(item.get("title")))
            for item in items
        )
        >= 2
    ]
    if eligible_crossref:
        latest_crossref_key, _latest_items = max(
            eligible_crossref,
            key=lambda group: _issue_key(*group[0]),
        )
        if _issue_key(*latest_crossref_key) > _issue_key(volume, issue):
            issue_data = fetch_crossref_current_issue(
                journal_id=journal_id,
                journal_name=journal_name,
                issn=issn,
                current_issue_url=current_issue_url,
                repec_jpe=repec_jpe,
                session=client,
                timeout=timeout,
            )
            issue_data["quality"]["flags"] = [
                "publisher_rss_lag_crossref_fallback"
                if flag == "publisher_html_blocked_crossref_fallback"
                else flag
                for flag in issue_data["quality"]["flags"]
            ]
            issue_data["quality"]["publisher_rss_issue"] = f"{volume}-{issue}"
            issue_data["quality"]["roster_transport"] = "crossref-newer-than-rss"
            return issue_data

    excluded_items: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []
    expected_rss_items = [
        item
        for item in current_items
        if not NON_RESEARCH_PATTERN.search(item["title"])
    ]
    current_items.sort(
        key=lambda item: (
            _page_start(item["page_start"]),
            item["title"],
        )
    )
    for rss_item in current_items:
        title = rss_item["title"]
        if not rss_item["page_start"] or NON_RESEARCH_PATTERN.search(title):
            excluded_items.append(
                {
                    "title_en": title,
                    "reason": (
                        "non_research_title"
                        if NON_RESEARCH_PATTERN.search(title)
                        else "page_missing_or_ancillary_item"
                    ),
                    "doi": rss_item["doi"],
                }
            )
            continue
        doi = rss_item["doi"]
        crossref = crossref_by_doi.get(doi, {})
        authors = _authors(crossref)
        abstract = _clean_markup(str(crossref.get("abstract", "")))
        abstract_source = "crossref" if abstract else ""
        rss_description = re.sub(
            r"^abstract\s*", "", rss_item["description"], flags=re.IGNORECASE
        ).strip()
        if not abstract and len(rss_description) >= 80:
            abstract = rss_description
            abstract_source = "publisher-rss"
        repec_url = ""
        if not abstract and (repec_jpe or repec_series_code) and doi:
            try:
                abstract, repec_url = _repec_abstract(
                    client,
                    doi,
                    timeout=timeout,
                    series_code=repec_series_code or "ucp/jpolec",
                )
            except requests.RequestException:
                abstract = ""
            if abstract:
                abstract_source = "repec-publisher-supplied"
        no_abstract_notice = _is_no_abstract_notice(abstract)
        if no_abstract_notice:
            abstract = ""
            abstract_source = ""
        flags = ["title_cn_missing", "abstract_cn_missing"]
        if not doi:
            flags.append("doi_missing")
        if not authors:
            flags.append("authors_missing")
        if not abstract:
            flags.append("abstract_en_missing")
        sequence = len(articles) + 1
        articles.append(
            {
                "paper_id": f"doi:{doi}" if doi else f"{journal_id}:{sequence}",
                "sequence": sequence,
                "source_sequence": _page_start(rss_item["page_start"]),
                "article_type": "comment" if no_abstract_notice else _article_type(title),
                "title_en": title or _clean_markup(_first(crossref.get("title"))),
                "title_cn": "",
                "authors": authors,
                "abstract_en": abstract,
                "abstract_cn": "",
                "doi": doi,
                "source_url": rss_item["link"]
                or (f"https://doi.org/{doi}" if doi else current_issue_url),
                "publication_date": rss_item["cover_date"],
                "sources": {
                    "issue": current_issue_url,
                    "roster": rss_url,
                    "metadata": "crossref",
                    "abstract_en": abstract_source,
                    **({"repec": repec_url} if repec_url else {}),
                },
                "translation": {
                    "status": "blocked" if not abstract else "pending",
                    "provider": "",
                    "prompt_version": "",
                    "glossary_version": "1",
                },
                "quality_flags": flags,
            }
        )

    if not articles:
        raise MetadataFallbackError("publisher RSS current issue has no research items")
    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    article_dois = set(doi_values)
    crossref_research_dois = {
        str(item.get("DOI", "")).strip().lower()
        for item in crossref_groups.get((volume, issue), [])
        if not NON_RESEARCH_PATTERN.search(_first(item.get("title")))
        and str(item.get("DOI", "")).strip()
    }
    expected_article_count = max(
        len(expected_rss_items),
        len(crossref_research_dois),
    )
    roster_match = (
        len(articles) == len(expected_rss_items)
        and (
            not crossref_research_dois
            or crossref_research_dois.issubset(article_dois)
        )
    )
    abstract_complete = sum(_has_abstract_or_allowed_comment(article) for article in articles)
    flags = ["translation_incomplete"]
    if not roster_match:
        flags.append("publisher_rss_roster_incomplete_crossref")
    if abstract_complete != len(articles):
        flags.append("abstract_en_incomplete")
    if duplicate_count:
        flags.append("duplicate_doi")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cover_date = next(
        (item["cover_date"] for item in current_items if item["cover_date"]), ""
    )
    return {
        "schema_version": "1.0",
        "issue_id": f"{journal_id}-{volume}-{issue}",
        "journal_id": journal_id,
        "journal_name": journal_name,
        "volume": volume,
        "issue": issue,
        "publication_date": cover_date
        or _publication_date(issn, volume, issue, list(crossref_by_doi.values())),
        "source_url": current_issue_url,
        "retrieved_at": now,
        "expected_article_count": expected_article_count,
        "research_article_count": len(articles),
        "status": "incomplete",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": roster_match,
            "order_preserved": True,
            "roster_authority": "publisher-rss",
            "roster_crosscheck": "crossref",
            "roster_transport": "publisher-rss",
            "rss_url": rss_url,
            "excluded_item_count": len(excluded_items),
            "excluded_items": excluded_items,
            "doi_complete": sum(bool(article["doi"]) for article in articles),
            "authors_complete": sum(bool(article["authors"]) for article in articles),
            "abstract_en_complete": abstract_complete,
            "translation_complete": 0,
            "duplicate_count": duplicate_count,
            "flags": flags,
        },
    }


def fetch_crossref_current_issue(
    *,
    journal_id: str,
    journal_name: str,
    issn: str,
    current_issue_url: str,
    repec_jpe: bool = False,
    repec_series_code: str = "",
    target_volume: str | None = None,
    target_issue: str | None = None,
    output_issue: str | None = None,
    future_cutoff: datetime | None = None,
    items_override: list[dict[str, Any]] | None = None,
    start_year: int | None = None,
    session: requests.Session | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Build a current-issue snapshot when a publisher blocks automated HTML.

    Crossref supplies the issue roster, DOI, authors, pages and deposited
    abstracts. For JPE only, missing abstracts are enriched from RePEc metadata
    supplied by the publisher. The original publisher URL remains the public
    source URL and every field records its actual transport source.
    """

    client = session or _session()
    items = items_override or _crossref_items(
        issn,
        session=client,
        timeout=timeout,
        start_year=start_year,
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        volume = str(item.get("volume", "")).strip()
        issue = str(item.get("issue", "")).strip()
        if not volume or item.get("type") != "journal-article":
            continue
        groups.setdefault((volume, issue), []).append(item)
    eligible = [
        (key, value)
        for key, value in groups.items()
        if _issue_is_not_future(
            issn,
            key[0],
            key[1],
            value,
            now=future_cutoff,
        )
        if sum(
            bool(item.get("DOI"))
            and not NON_RESEARCH_PATTERN.search(_first(item.get("title")))
            for item in value
        )
        >= 2
    ]
    if not eligible:
        raise MetadataFallbackError("Crossref returned no usable recent issue")
    if target_volume is not None and target_issue is not None:
        issue_items = dict(eligible).get((str(target_volume), str(target_issue)))
        if issue_items is None:
            raise MetadataFallbackError(
                f"Crossref returned no usable issue {target_volume}/{target_issue}"
            )
        volume, issue = str(target_volume), str(target_issue)
    else:
        (volume, issue), issue_items = max(
            eligible,
            key=lambda group: (_number(group[0][0]), _number(group[0][1])),
        )

    excluded_items: list[dict[str, Any]] = []
    research_items: list[dict[str, Any]] = []
    research_candidates = [
        item
        for item in issue_items
        if not NON_RESEARCH_PATTERN.search(_first(item.get("title")))
    ]
    for item in issue_items:
        title = _first(item.get("title"))
        reason = ""
        if NON_RESEARCH_PATTERN.search(title):
            reason = "non_research_title"
        if reason:
            excluded_items.append(
                {
                    "title_en": title,
                    "reason": reason,
                    "doi": str(item.get("DOI", "")).lower(),
                }
            )
        else:
            research_items.append(item)
    research_items.sort(
        key=lambda item: (
            _page_start(_locator(item)),
            _locator(item),
            _first(item.get("title")),
        )
    )

    articles: list[dict[str, Any]] = []
    for sequence, item in enumerate(research_items, start=1):
        doi = str(item.get("DOI", "")).strip().lower()
        title = _clean_markup(_first(item.get("title")))
        abstract = _clean_markup(str(item.get("abstract", "")))
        abstract_source = "crossref" if abstract else ""
        elsevier_url = ""
        elsevier_lookup: dict[str, Any] = {}
        abstract_snippet = ""
        if not abstract and _is_elsevier_identifier(_crossref_pii(item), doi):
            elsevier_lookup = _elsevier_lookup(
                client,
                _crossref_pii(item),
                doi=doi,
                timeout=timeout,
            )
            abstract = str(elsevier_lookup.get("abstract", ""))
            abstract_snippet = str(elsevier_lookup.get("teaser", ""))
            elsevier_url = str(elsevier_lookup.get("source_url", ""))
            if abstract:
                abstract_source = str(elsevier_lookup.get("source", "elsevier-api"))
        repec_url = ""
        if not abstract and (repec_jpe or repec_series_code) and doi:
            try:
                abstract, repec_url = _repec_abstract(
                    client,
                    doi,
                    timeout=timeout,
                    series_code=repec_series_code or "ucp/jpolec",
                )
            except requests.RequestException:
                abstract = ""
            if abstract:
                abstract_source = "repec-publisher-supplied"
        authors = _authors(item)
        openalex_url = ""
        if doi and (not authors or not abstract):
            openalex_authors, openalex_abstract, openalex_url = _openalex_metadata(
                client, doi, timeout=timeout
            )
            if not authors and openalex_authors:
                authors = openalex_authors
            if not abstract and openalex_abstract:
                abstract = openalex_abstract
                abstract_source = "openalex"
        flags = ["title_cn_missing", "abstract_cn_missing"]
        if not doi:
            flags.append("doi_missing")
        if not authors:
            flags.append("authors_missing")
        if not abstract:
            flags.append("abstract_en_missing")
            if abstract_snippet:
                flags.append("abstract_teaser_only")
        source_url = f"https://doi.org/{doi}" if doi else str(item.get("URL", ""))
        articles.append(
            {
                "paper_id": f"doi:{doi}" if doi else f"{journal_id}:{sequence}",
                "sequence": sequence,
                "source_sequence": _page_start(_locator(item)),
                "article_type": _article_type(title),
                "title_en": title,
                "title_cn": "",
                "authors": authors,
                "abstract_en": abstract,
                "abstract_cn": "",
                "abstract_snippet_en": abstract_snippet,
                "doi": doi,
                "source_url": source_url,
                "publication_date": _date_year(item),
                "sources": {
                    "issue": current_issue_url,
                    "roster": f"crossref:issn:{issn}",
                    "metadata": "crossref",
                    "abstract_en": abstract_source,
                    **({"elsevier": elsevier_url} if elsevier_url else {}),
                    **(
                        {
                            "abstract_lookup": {
                                "status": elsevier_lookup.get("status", ""),
                                "attempts": elsevier_lookup.get("attempts", []),
                                **(
                                    {"rate_limit": elsevier_lookup["rate_limit"]}
                                    if elsevier_lookup.get("rate_limit")
                                    else {}
                                ),
                                **(
                                    {
                                        "quota_warning": elsevier_lookup[
                                            "quota_warning"
                                        ]
                                    }
                                    if elsevier_lookup.get("quota_warning")
                                    else {}
                                ),
                            }
                        }
                        if elsevier_lookup
                        else {}
                    ),
                    **({"repec": repec_url} if repec_url else {}),
                    **({"openalex": openalex_url} if openalex_url else {}),
                },
                "translation": {
                    "status": "blocked" if not abstract else "pending",
                    "provider": "",
                    "prompt_version": "",
                    "glossary_version": "1",
                },
                "quality_flags": flags,
            }
        )

    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    roster_match = len(research_items) == len(research_candidates)
    flags = [
        "publisher_html_blocked_crossref_fallback",
        "crossref_provisional_roster",
        "translation_incomplete",
    ]
    if not roster_match:
        flags.append("crossref_roster_incomplete")
    abstract_complete = sum(_has_abstract_or_allowed_comment(article) for article in articles)
    if abstract_complete != len(articles):
        flags.append("abstract_en_incomplete")
    if _missing_insttoken(articles):
        flags.append("elsevier_insttoken_required")
    if duplicate_count:
        flags.append("duplicate_doi")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    published_issue = output_issue if output_issue is not None else issue
    return {
        "schema_version": "1.0",
        "issue_id": f"{journal_id}-{volume}-{published_issue.lower()}",
        "journal_id": journal_id,
        "journal_name": journal_name,
        "volume": volume,
        "issue": published_issue,
        "publication_date": _publication_date(issn, volume, issue, issue_items),
        "source_url": current_issue_url,
        "retrieved_at": now,
        "expected_article_count": len(research_candidates),
        "research_article_count": len(articles),
        "status": "incomplete",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": roster_match,
            "order_preserved": True,
            "roster_transport": "crossref",
            "roster_authority": "crossref-provisional",
            "roster_match_scope": "crossref-issue-group",
            "publisher_page_status": "blocked",
            "excluded_item_count": len(excluded_items),
            "excluded_items": excluded_items,
            "doi_complete": sum(bool(article["doi"]) for article in articles),
            "authors_complete": sum(bool(article["authors"]) for article in articles),
            "abstract_en_complete": abstract_complete,
            "translation_complete": 0,
            "duplicate_count": duplicate_count,
            "flags": flags,
        },
    }


def fetch_elsevier_issue_via_search(
    *,
    journal_id: str,
    journal_name: str,
    issn: str,
    volume: str,
    issue: str = "c",
    official_issue_url: str = "",
    session: requests.Session | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Build one Elsevier issue from the ScienceDirect Search API roster.

    RePEc/Crossref coverage of some Elsevier volumes is incomplete. The
    ScienceDirect Search API indexes the publisher's own per-volume article
    lists, so it is used as a roster authority for history; missing abstracts
    are filled through the Article Metadata chain.
    """

    client = session or requests.Session()
    api_key = os.getenv("ELSEVIER_API_KEY", "").strip()
    inst_token = os.getenv("ELSEVIER_INST_TOKEN", "").strip()
    if not api_key:
        raise MetadataFallbackError("ELSEVIER_API_KEY is not configured")
    headers = {"Accept": "application/xml", "X-ELS-APIKey": api_key}
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token

    def search_page(cursor: str) -> ElementTree.Element | None:
        params = {
            "query": f"ISSN({issn}) AND VOLUME({volume})",
            "field": "url,identifier,doi,pii,title,creator,description,coverDate",
            "count": "100",
            "httpAccept": "application/xml",
            "sort": "coverDate",
        }
        if cursor != "*":
            params["cursor"] = cursor
        else:
            params["cursor"] = "*"
        response = client.get(
            ELSEVIER_SEARCH_API,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return ElementTree.fromstring(response.content)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "dc": "http://purl.org/dc/elements/1.1/",
        "prism": "http://prismstandard.org/namespaces/basic/2.0/",
        "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
        "sci": "http://www.elsevier.com/xml/schemas/sciencedirect",
    }

    entries: list[dict[str, Any]] = []
    cursor: str = "*"
    seen_total = 0
    for _ in range(20):
        root = search_page(cursor)
        if root is None:
            break
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("dc:title", "", ns).strip()
            doi = entry.findtext("prism:doi", "", ns).strip()
            pii = entry.findtext("pii", "", ns).strip() or entry.findtext("sci:pii", "", ns)
            cover_date = entry.findtext("prism:coverDate", "", ns).strip()
            description = entry.findtext("dc:description", "", ns).strip()
            creators = [
                node.text.strip()
                for node in entry.findall("dc:creator", ns)
                if node.text and node.text.strip()
            ]
            if not title:
                continue
            entries.append(
                {
                    "title": title,
                    "doi": doi,
                    "pii": re.sub(r"[^A-Za-z0-9]", "", pii or ""),
                    "cover_date": cover_date,
                    "description": description,
                    "creators": creators,
                }
            )
        total = root.findtext("opensearch:totalResults", "", ns)
        if total.isdigit():
            seen_total = int(total)
        next_cursor = root.findtext("opensearch:nextCursor", "", ns) or root.findtext("cursor", "", ns)
        if not next_cursor or next_cursor == cursor or len(entries) >= max(int(total or "0"), len(entries)):
            break
        cursor = next_cursor

    if not entries:
        raise MetadataFallbackError(
            f"Elsevier Search API returned no items for ISSN {issn} volume {volume}"
        )

    def resolve(entry: dict[str, Any]) -> dict[str, Any]:
        title = entry["title"]
        doi = entry["doi"]
        pii = entry["pii"]
        authors = list(entry["creators"])
        abstract = entry["description"]
        abstract_source = "elsevier-search-description" if abstract else ""
        source_url = (
            f"https://www.sciencedirect.com/science/article/pii/{pii}"
            if pii
            else (f"https://doi.org/{doi}" if doi else official_issue_url)
        )
        if not abstract and (doi or pii):
            lookup = _elsevier_lookup(client, pii, doi=doi, timeout=timeout)
            fetched = str(lookup.get("abstract", "")).strip()
            if fetched:
                abstract = fetched
                abstract_source = str(lookup.get("source", "elsevier-api"))
        no_abstract_notice = _is_no_abstract_notice(abstract)
        if no_abstract_notice:
            abstract = ""
            abstract_source = ""
        flags = ["title_cn_missing", "abstract_cn_missing"]
        if not doi:
            flags.append("doi_missing")
        if not authors:
            flags.append("authors_missing")
        if not abstract:
            flags.append("abstract_en_missing")
        return {
            "paper_id": f"doi:{doi}" if doi else (f"pii:{pii}" if pii else f"{journal_id}:{title}"),
            "sequence": 0,
            "source_sequence": 0,
            "article_type": "comment" if no_abstract_notice else _article_type(title),
            "title_en": title,
            "title_cn": "",
            "authors": authors,
            "abstract_en": abstract,
            "abstract_cn": "",
            "doi": doi,
            "source_url": source_url,
            "publication_date": entry["cover_date"] or volume,
            "sources": {
                "issue": official_issue_url or ELSEVIER_SEARCH_API,
                "roster": "elsevier-search-api",
                "metadata": "elsevier-search-api",
                "abstract_en": abstract_source,
            },
            "translation": {
                "status": "blocked" if not abstract else "pending",
                "provider": "",
                "prompt_version": "",
                "glossary_version": "1",
            },
            "quality_flags": flags,
        }

    built_items = [resolve(entry) for entry in entries]
    excluded_items: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []
    for article in built_items:
        if NON_RESEARCH_PATTERN.search(article["title_en"]):
            excluded_items.append(
                {
                    "title_en": article["title_en"],
                    "reason": "non_research_title",
                    "doi": article["doi"],
                }
            )
            continue
        article["sequence"] = len(articles) + 1
        article["source_sequence"] = article["sequence"]
        articles.append(article)

    if not articles:
        raise MetadataFallbackError(
            f"Elsevier Search API volume {volume} has no publishable articles"
        )
    doi_values = [article["doi"] for article in articles if article["doi"]]
    duplicate_count = len(doi_values) - len(set(doi_values))
    abstract_complete = sum(
        _has_abstract_or_allowed_comment(article) for article in articles
    )
    authors_complete = sum(bool(article["authors"]) for article in articles)
    flags = ["translation_incomplete"]
    if duplicate_count:
        flags.append("duplicate_doi")
    if abstract_complete != len(articles):
        flags.append("abstract_en_incomplete")
    if authors_complete != len(articles):
        flags.append("authors_incomplete")

    issue_id = f"{journal_id}-{volume}-{str(issue).lower()}"
    return {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": journal_id,
        "journal_name": journal_name,
        "volume": str(volume),
        "issue": str(issue),
        "issue_label": f"Vol. {volume}",
        "publication_date": str(entries[0].get("cover_date") or volume),
        "source_url": official_issue_url or ELSEVIER_SEARCH_API,
        "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "expected_article_count": len(articles),
        "research_article_count": len(articles),
        "status": "incomplete",
        "publication_state": "enriching",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_authority": "elsevier-search-api",
            "roster_transport": "elsevier-search-api",
            "official_item_count": len(articles) + len(excluded_items),
            "observed_items": len(articles) + len(excluded_items),
            "excluded_items": excluded_items,
            "excluded_item_count": len(excluded_items),
            "publishable_item_count": len(articles),
            "doi_complete": len(doi_values),
            "authors_complete": authors_complete,
            "abstract_en_complete": abstract_complete,
            "translation_complete": 0,
            "duplicate_count": duplicate_count,
            "flags": flags,
        },
    }
