from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
CROSSREF_API = "https://api.crossref.org"
NON_RESEARCH_PATTERN = re.compile(
    r"front\s*matter|back\s*matter|editorial\s*board|table\s*of\s*contents|"
    r"recent\s*referees|turnaround\s*times|"
    r"^correction(?:\s+to\b|:|\s*$)|^erratum(?:\s+to\b|:|\s*$)|"
    r"\ba\s+comment\b|^comment(?:\s+on)?\b|^reply(?:\s+to)?\b|"
    r"submission\s+of\s+manuscripts",
    re.IGNORECASE,
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


def _clean_markup(value: str) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(html.unescape(value), "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


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


def _repec_jpe_url(doi: str) -> str:
    return f"https://ideas.repec.org/a/ucp/jpolec/doi{doi.replace('/', '-')}.html"


def _repec_abstract(
    session: requests.Session,
    doi: str,
    *,
    timeout: int,
) -> tuple[str, str]:
    url = _repec_jpe_url(doi)
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


def _crossref_items(
    issn: str,
    *,
    session: requests.Session,
    timeout: int,
) -> list[dict[str, Any]]:
    start_year = datetime.now(timezone.utc).year - 1
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


def fetch_official_rss_issue(
    *,
    journal_id: str,
    journal_name: str,
    issn: str,
    current_issue_url: str,
    rss_url: str,
    repec_jpe: bool = False,
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
            bool(item.get("page"))
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
        if not abstract and repec_jpe and doi:
            try:
                abstract, repec_url = _repec_abstract(client, doi, timeout=timeout)
            except requests.RequestException:
                abstract = ""
            if abstract:
                abstract_source = "repec-publisher-supplied"
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
                "article_type": "research-article",
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
    abstract_complete = sum(bool(article["abstract_en"]) for article in articles)
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
    items = _crossref_items(issn, session=client, timeout=timeout)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        volume = str(item.get("volume", "")).strip()
        issue = str(item.get("issue", "")).strip()
        if not volume or not issue or item.get("type") != "journal-article":
            continue
        groups.setdefault((volume, issue), []).append(item)
    eligible = [
        (key, value)
        for key, value in groups.items()
        if _issue_is_not_future(issn, key[0], key[1], value)
        if sum(
            bool(item.get("page"))
            and not NON_RESEARCH_PATTERN.search(_first(item.get("title")))
            for item in value
        )
        >= 2
    ]
    if not eligible:
        raise MetadataFallbackError("Crossref returned no usable recent issue")
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
        if not item.get("page"):
            reason = "page_missing_or_ancillary_item"
        elif NON_RESEARCH_PATTERN.search(title):
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
    research_items.sort(key=lambda item: (_page_start(str(item.get("page", ""))), _first(item.get("title"))))

    articles: list[dict[str, Any]] = []
    for sequence, item in enumerate(research_items, start=1):
        doi = str(item.get("DOI", "")).strip().lower()
        title = _clean_markup(_first(item.get("title")))
        abstract = _clean_markup(str(item.get("abstract", "")))
        abstract_source = "crossref" if abstract else ""
        repec_url = ""
        if not abstract and repec_jpe and doi:
            try:
                abstract, repec_url = _repec_abstract(client, doi, timeout=timeout)
            except requests.RequestException:
                abstract = ""
            if abstract:
                abstract_source = "repec-publisher-supplied"
        authors = _authors(item)
        flags = ["title_cn_missing", "abstract_cn_missing"]
        if not doi:
            flags.append("doi_missing")
        if not authors:
            flags.append("authors_missing")
        if not abstract:
            flags.append("abstract_en_missing")
        source_url = f"https://doi.org/{doi}" if doi else str(item.get("URL", ""))
        articles.append(
            {
                "paper_id": f"doi:{doi}" if doi else f"{journal_id}:{sequence}",
                "sequence": sequence,
                "source_sequence": _page_start(str(item.get("page", ""))),
                "article_type": "research-article",
                "title_en": title,
                "title_cn": "",
                "authors": authors,
                "abstract_en": abstract,
                "abstract_cn": "",
                "doi": doi,
                "source_url": source_url,
                "publication_date": _date_year(item),
                "sources": {
                    "issue": current_issue_url,
                    "roster": f"crossref:issn:{issn}",
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
    abstract_complete = sum(bool(article["abstract_en"]) for article in articles)
    if abstract_complete != len(articles):
        flags.append("abstract_en_incomplete")
    if duplicate_count:
        flags.append("duplicate_doi")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "schema_version": "1.0",
        "issue_id": f"{journal_id}-{volume}-{issue}",
        "journal_id": journal_id,
        "journal_name": journal_name,
        "volume": volume,
        "issue": issue,
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
