from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup, Tag


HOST = "www.econometricsociety.org"
VOLUME_PATTERN = re.compile(r"\bEconometrica\s*-?\s*Volume\s+(?P<volume>\d+)\b", re.IGNORECASE)
ISSUE_PATTERN = re.compile(
    r"^Issue\s+(?P<issue>\d+)\s*\((?P<period>"
    r"January|February|March|April|May|June|July|August|September|October|November|December"
    r")\s+(?P<year>20\d{2})\)$",
    re.IGNORECASE,
)


def _official_url(source_url: str) -> bool:
    parsed = urlparse(source_url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == HOST
        and parsed.path.startswith("/publications/econometrica")
    )


def _text(node: Any) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def parse_latest_econometrica_issue_signal(
    content: bytes,
    source_url: str,
) -> dict[str, str]:
    """Parse latest Econometrica issue identity from the Society's official page.

    This is issue-existence evidence only. It does not assert the article roster,
    ordering, abstract completeness, translation readiness, or publication readiness.
    """

    if not _official_url(source_url):
        raise ValueError("Econometrica issue signals require the official Society HTTPS host")

    soup = BeautifulSoup(content, "html.parser")
    page_text = _text(soup)
    volume_match = VOLUME_PATTERN.search(page_text)
    if not volume_match:
        raise ValueError("Econometric Society page exposed no Econometrica volume identity")
    volume = volume_match.group("volume")

    candidates: list[tuple[int, dict[str, str]]] = []
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        match = ISSUE_PATTERN.fullmatch(_text(heading))
        if not match:
            continue
        issue = match.group("issue")
        publication_date = f"{match.group('period').title()} {match.group('year')}"
        candidates.append(
            (
                int(issue),
                {
                    "volume": volume,
                    "issue": issue,
                    "publication_date": publication_date,
                    "source_kind": "association_announcement",
                    "source_url": source_url,
                },
            )
        )

    if not candidates:
        raise ValueError("Econometric Society page exposed no auditable issue headings")
    return max(candidates, key=lambda item: item[0])[1]


ARTICLE_PATH_PATTERN = re.compile(
    r"^/publications/econometrica/(?P<year>20\d{2})/(?P<month>\d{2})/(?P<day>\d{2})/(?P<slug>[^/?#]+)$",
    re.IGNORECASE,
)
STRUCTURAL_ITEM_PATTERN = re.compile(
    r"front\s*matter|frontmatter|back\s*matter|backmatter|"
    r"submission\s+of\s+manuscripts|table\s+of\s+contents",
    re.IGNORECASE,
)


def _normalise_doi(value: str) -> str:
    return str(value or "").strip().lower().removeprefix("https://doi.org/").rstrip(".,;:)]}")


def _doi_from_auth_link(href: str, source_url: str) -> str:
    parsed = urlparse(href or "")
    if not parsed.netloc:
        base = urlparse(source_url)
        parsed = parsed._replace(scheme=base.scheme, netloc=base.netloc)
    if (parsed.hostname or "").casefold() != HOST:
        return ""
    if not parsed.path.startswith("/member-authentication/"):
        return ""
    values = parse_qs(parsed.query).get("doi", [])
    return _normalise_doi(values[0]) if values else ""


def _article_identity(href: str, source_url: str) -> tuple[str, str]:
    parsed = urlparse(href or "")
    if not parsed.netloc:
        base = urlparse(source_url)
        parsed = parsed._replace(scheme=base.scheme, netloc=base.netloc)
    if (parsed.hostname or "").casefold() != HOST:
        return "", ""
    match = ARTICLE_PATH_PATTERN.fullmatch(parsed.path)
    if not match:
        return "", ""
    canonical = f"https://{HOST}{parsed.path}"
    return canonical, match.group("slug")


def parse_latest_econometrica_roster(
    content: bytes,
    source_url: str,
) -> dict[str, Any]:
    """Parse the latest Econometrica issue roster from the Society's first-party page.

    Unlike the lightweight announcement signal, this parser requires an auditable
    per-article sequence and DOI for every research item. Structural front/back
    matter is retained as excluded evidence, never promoted as research content.
    """

    signal = parse_latest_econometrica_issue_signal(content, source_url)
    soup = BeautifulSoup(content, "html.parser")
    target_heading: Tag | None = None
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        match = ISSUE_PATTERN.fullmatch(_text(heading))
        if (
            match
            and match.group("issue") == signal["issue"]
            and f"{match.group('period').title()} {match.group('year')}"
            == signal["publication_date"]
        ):
            target_heading = heading
            break
    if target_heading is None:
        raise ValueError("Econometric Society latest issue heading could not be isolated")

    raw_items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    seen_paths: set[str] = set()
    for node in target_heading.find_all_next(["h1", "h2", "h3", "h4", "a"]):
        if node is target_heading:
            continue
        if node.name in {"h1", "h2", "h3", "h4"}:
            next_issue = ISSUE_PATTERN.fullmatch(_text(node))
            if next_issue:
                break
            continue
        href = str(node.get("href") or "")
        canonical, slug = _article_identity(href, source_url)
        if canonical:
            if canonical in seen_paths:
                continue
            seen_paths.add(canonical)
            title = _text(node) or slug.replace("-", " ")
            current = {
                "title_en": title,
                "source_url": canonical,
                "doi": "",
                "excluded": bool(STRUCTURAL_ITEM_PATTERN.search(f"{title} {slug}")),
            }
            raw_items.append(current)
            continue
        doi = _doi_from_auth_link(href, source_url)
        if doi and current and not current.get("doi"):
            current["doi"] = doi

    if not raw_items:
        raise ValueError("Econometric Society latest issue exposed no article roster")

    research: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_dois: set[str] = set()
    for item in raw_items:
        doi = _normalise_doi(item.get("doi", ""))
        if item["excluded"]:
            excluded.append(
                {
                    "title_en": item["title_en"],
                    "reason": "structural_item",
                    "doi": doi,
                    "source_url": item["source_url"],
                }
            )
            continue
        if not doi:
            raise ValueError(
                "Econometric Society research roster item has no auditable DOI: "
                + str(item["title_en"])
            )
        if doi in seen_dois:
            raise ValueError(f"Econometric Society roster contains duplicate DOI {doi}")
        seen_dois.add(doi)
        research.append(
            {
                "sequence": len(research) + 1,
                "title_en": item["title_en"],
                "doi": doi,
                "source_url": item["source_url"],
            }
        )
    if not research:
        raise ValueError("Econometric Society latest issue exposed no research articles")

    return {
        **signal,
        "source_kind": "official_issue_page",
        "articles": research,
        "excluded_items": excluded,
        "research_article_count": len(research),
        "official_item_count": len(raw_items),
    }


def qualify_crossref_issue_with_econometric_society(
    issue: dict[str, Any],
    roster: dict[str, Any],
) -> dict[str, Any]:
    """Promote metadata only after exact DOI equality with the first-party roster."""

    if str(issue.get("volume", "")) != str(roster.get("volume", "")):
        raise ValueError("Econometric Society roster volume does not match candidate")
    if str(issue.get("issue", "")).casefold() != str(roster.get("issue", "")).casefold():
        raise ValueError("Econometric Society roster issue does not match candidate")

    by_doi = {
        _normalise_doi(article.get("doi", "")): article
        for article in issue.get("articles", [])
        if _normalise_doi(article.get("doi", ""))
    }
    official_dois = [_normalise_doi(item.get("doi", "")) for item in roster["articles"]]
    if set(by_doi) != set(official_dois) or len(by_doi) != len(official_dois):
        missing = sorted(set(official_dois) - set(by_doi))
        extra = sorted(set(by_doi) - set(official_dois))
        raise ValueError(
            f"Econometric Society/Crossref DOI roster mismatch; missing={missing}; extra={extra}"
        )

    ordered: list[dict[str, Any]] = []
    for sequence, official in enumerate(roster["articles"], start=1):
        article = by_doi[_normalise_doi(official["doi"])]
        article["sequence"] = sequence
        article["source_sequence"] = sequence
        article["source_url"] = official["source_url"]
        sources = article.setdefault("sources", {})
        sources["issue"] = roster["source_url"]
        sources["roster"] = roster["source_url"]
        sources["official_article"] = official["source_url"]
        ordered.append(article)
    issue["articles"] = ordered
    issue["source_url"] = roster["source_url"]
    issue["publication_date"] = roster["publication_date"]
    issue["expected_article_count"] = len(ordered)
    issue["research_article_count"] = len(ordered)
    quality = issue.setdefault("quality", {})
    quality["roster_match"] = True
    quality["order_preserved"] = True
    quality["roster_authority"] = "official-issue-page"
    quality["roster_transport"] = "econometric-society-page"
    quality["roster_match_scope"] = "official-doi-exact"
    quality["official_item_count"] = int(roster.get("official_item_count", len(ordered)))
    quality["excluded_items"] = list(roster.get("excluded_items", []))
    quality["excluded_item_count"] = len(quality["excluded_items"])
    blocked_flags = {
        "crossref_provisional_roster",
        "publisher_html_blocked_crossref_fallback",
        "publisher_rss_lag_crossref_fallback",
        "official_order_unverified",
    }
    quality["flags"] = [
        flag for flag in quality.get("flags", []) if flag not in blocked_flags
    ]
    return issue


def fetch_latest_econometrica_issue(
    *,
    source_url: str,
    journal_id: str,
    journal_name: str,
    issn: str,
    current_issue_url: str,
    repec_series_code: str = "",
    session: requests.Session | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Build the latest issue from official Society roster + metadata transports."""

    client = session or requests.Session()
    client.headers.update(
        {
            "User-Agent": "AcademicDoorJournals/1.0 (https://academic-door.github.io/)",
        }
    )
    response = client.get(
        source_url,
        timeout=timeout,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    response.raise_for_status()
    roster = parse_latest_econometrica_roster(response.content, source_url)

    # Local import keeps the first-party parser independent of metadata transports.
    from collectors.metadata_fallback import fetch_crossref_current_issue

    candidate = fetch_crossref_current_issue(
        journal_id=journal_id,
        journal_name=journal_name,
        issn=issn,
        current_issue_url=current_issue_url,
        repec_series_code=repec_series_code,
        target_volume=str(roster["volume"]),
        target_issue=str(roster["issue"]),
        session=client,
        timeout=timeout,
    )
    return qualify_crossref_issue_with_econometric_society(candidate, roster)
