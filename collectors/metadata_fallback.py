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
NON_RESEARCH_PATTERN = re.compile(
    r"front\s*matter|back\s*matter|editorial\s*board|table\s*of\s*contents|"
    r"recent\s*referees|turnaround\s*times|issue\s+information|"
    r"^announcements?$|american\s+finance\s+association|annual\s+report|"
    r"report\s+of\s+the\s+editor.+for\s+the\s+year|"
    r"summaries\s+of\s+doctoral\s+dissertations|"
    r"abstracts\s+of\s+papers\s+presented|editors?[’'＊]?\s+notes?|"
    r"^editorial(?:\s*:|\s*$)|"
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
    r"\ba\s+comment\b|^comment(?:\s+on)?\b|^reply(?:\s+to)?\b|:\s*(?:a\s+)?comment\s*$|:\s*reply\s*$",
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


def _elsevier_text(root: ElementTree.Element, names: set[str]) -> str:
    for node in root.iter():
        if _local_name(node.tag) not in names:
            continue
        value = _clean_markup(" ".join(node.itertext()))
        if value and not _is_no_abstract_notice(value):
            return value
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
            status_code = int(getattr(response, "status_code", 200))
            if status_code >= 400:
                result["attempts"].append(
                    {
                        "source": name,
                        "status_code": status_code,
                        "outcome": _elsevier_status(status_code),
                    }
                )
                result["status"] = _elsevier_status(status_code)
                if status_code == 403 and not inst_token:
                    result["status"] = "insufficient_entitlement_missing_insttoken"
                return None
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except requests.RequestException:
            result["attempts"].append(
                {"source": name, "status_code": 0, "outcome": "temporary_error"}
            )
            result["status"] = "temporary_error"
            return None
        except ElementTree.ParseError:
            result["attempts"].append(
                {"source": name, "status_code": 200, "outcome": "invalid_response"}
            )
            result["status"] = "invalid_response"
            return None
        result["attempts"].append(
            {"source": name, "status_code": status_code, "outcome": "success"}
        )
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
  …9832 tokens truncated…umber", "issue")
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

