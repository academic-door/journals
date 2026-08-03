"""Discover exact historical issue pages from official publisher archives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
VOLUME_ISSUE_PATTERN = re.compile(
    r"(?:Vol(?:ume)?\.?\s*)?(\d+)\s*[,·]?\s*"
    r"(?:No\.?|Number|Issue)\s*(\d+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HistoricalIssue:
    journal: str
    year: int
    volume: str
    issue: str
    official_url: str

    @property
    def issue_id(self) -> str:
        return f"{self.journal.casefold()}-{self.volume}-{self.issue}"


def _get(url: str) -> bytes:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"},
        timeout=(10, 45),
    )
    response.raise_for_status()
    return response.content


def _allowed(url: str, allowed_host: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").casefold() == allowed_host


def _add(
    found: dict[str, HistoricalIssue],
    *,
    journal: str,
    year: int,
    volume: str,
    issue: str,
    url: str,
    allowed_host: str,
) -> None:
    if _allowed(url, allowed_host):
        record = HistoricalIssue(journal, year, volume, issue, url)
        found[record.issue_id] = record


def parse_archive(
    content: bytes,
    archive_url: str,
    *,
    journal: str,
    platform: str,
    years: Iterable[int],
    allowed_host: str,
) -> list[HistoricalIssue]:
    """Parse publisher archive links without inferring article membership."""

    wanted_years = set(years)
    soup = BeautifulSoup(content, "html.parser")
    found: dict[str, HistoricalIssue] = {}
    for link in soup.select("a[href]"):
        href = urljoin(archive_url, link.get("href", ""))
        text = " ".join(link.get_text(" ", strip=True).split())
        path = urlparse(href).path

        year = 0
        volume = ""
        issue = ""
        if platform == "aea" and re.fullmatch(r"/issues/\d+", path):
            match = VOLUME_ISSUE_PATTERN.search(text)
            year_match = YEAR_PATTERN.search(text)
            if match and year_match:
                volume, issue = match.groups()
                year = int(year_match.group(1))
        elif platform == "chicago":
            match = re.fullmatch(r"/toc/jpe/(20\d{2})/(\d+)/(\d+)", path)
            if match:
                year, volume, issue = int(match.group(1)), match.group(2), match.group(3)
        elif platform == "oup":
            match = re.search(r"/(?:qje|restud)/issue/(\d+)/(\d+)", path)
            if match:
                volume, issue = match.groups()
                year_match = YEAR_PATTERN.search(text) or YEAR_PATTERN.search(archive_url)
                if year_match:
                    year = int(year_match.group(1))
        elif platform == "wiley":
            match = re.search(r"/toc/14680262/(20\d{2})/(\d+)/(\d+)", path)
            if match:
                year, volume, issue = int(match.group(1)), match.group(2), match.group(3)

        if year in wanted_years and volume and issue:
            _add(
                found,
                journal=journal,
                year=year,
                volume=volume,
                issue=issue,
                url=href.split("?", 1)[0].split("#", 1)[0],
                allowed_host=allowed_host,
            )
    return sorted(
        found.values(),
        key=lambda item: (item.year, int(item.volume), int(item.issue)),
    )


def _item_year(item: dict) -> int:
    for key in ("published-print", "published-online", "issued"):
        parts = item.get(key, {}).get("date-parts", [[None]])
        try:
            return int(parts[0][0])
        except (TypeError, ValueError, IndexError):
            continue
    return 0


def discover_crossref_issues(
    journal: str,
    issn: str,
    issue_url_template: str,
    *,
    years: Iterable[int],
    session: requests.Session | None = None,
) -> list[HistoricalIssue]:
    """Discover continuous-publishing Elsevier volumes from Crossref.

    Elsevier field journals publish one volume per issue (labelled C on
    ScienceDirect) and Crossref groups them by volume with an empty issue
    field. The plan is built from the distinct volumes whose articles fall
    inside the requested years; boundary volumes that span adjacent years are
    assigned to their most common year.
    """

    wanted = set(years)
    if not wanted:
        return []
    start_year = min(wanted) - 2
    end_year = max(wanted)
    client = session or requests.Session()
    found: dict[str, HistoricalIssue] = {}
    cursor = "*"
    while cursor:
        response = client.get(
            f"https://api.crossref.org/journals/{issn}/works",
            params={
                "filter": (
                    f"from-pub-date:{start_year}-01-01,"
                    f"until-pub-date:{end_year}-12-31,type:journal-article"
                ),
                "rows": "1000",
                "cursor": cursor,
                "select": "DOI,title,volume,issue,published-print,published-online,issued",
            },
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=(10, 60),
        )
        response.raise_for_status()
        payload = response.json().get("message", {})
        items = payload.get("items", [])
        if not items:
            # Crossref keeps returning the same next-cursor at the end of the
            # result set; an empty page is the only reliable stop signal.
            break
        for item in items:
            volume = str(item.get("volume", "")).strip()
            if not volume or not volume.isdigit():
                continue
            by_year = found.get(volume, {})
            by_year.setdefault(_item_year(item), 0)
            by_year[_item_year(item)] += 1
            found[volume] = by_year
        cursor = str(payload.get("next-cursor", "") or "")
    issues: dict[str, HistoricalIssue] = {}
    for volume, year_counts in found.items():
        year, _ = max(year_counts.items(), key=lambda pair: (pair[1], pair[0]))
        if year not in wanted:
            continue
        url = issue_url_template.format(
            volume=volume,
            issue="C",
            issue_lower="c",
            year=year,
        )
        _add(
            issues,
            journal=journal,
            year=year,
            volume=volume,
            issue="c",
            url=url,
            allowed_host=urlparse(url).hostname or "",
        )
    return sorted(issues.values(), key=lambda item: (item.year, int(item.volume)))


def discover_official_issues(
    journal: str,
    definition: dict,
    *,
    years: Iterable[int],
) -> list[HistoricalIssue]:
    found: dict[str, HistoricalIssue] = {}
    year_values = sorted(set(years))
    if definition.get("platform") == "crossref":
        return discover_crossref_issues(
            journal,
            definition["issn"],
            definition["issue_url_template"],
            years=year_values,
        )
    if definition.get("year_ranges"):
        for year in year_values:
            year_definition = definition["year_ranges"].get(str(year))
            if not year_definition:
                continue
            for issue_number in year_definition["issues"]:
                url = definition["issue_url_template"].format(
                    year=year,
                    volume=year_definition["volume"],
                    issue=issue_number,
                )
                _add(
                    found,
                    journal=journal,
                    year=year,
                    volume=str(year_definition["volume"]),
                    issue=str(issue_number),
                    url=url,
                    allowed_host=definition["allowed_host"],
                )
        return sorted(
            found.values(),
            key=lambda item: (item.year, int(item.volume), int(item.issue)),
        )
    archive_template = definition.get("archive_url_template")
    archive_urls = (
        [archive_template.format(year=year) for year in year_values]
        if archive_template
        else [definition["archive_url"]]
    )
    for archive_url in archive_urls:
        for issue in parse_archive(
            _get(archive_url),
            archive_url,
            journal=journal,
            platform=definition["platform"],
            years=year_values,
            allowed_host=definition["allowed_host"],
        ):
            found[issue.issue_id] = issue
    return sorted(
        found.values(),
        key=lambda item: (item.year, int(item.volume), int(item.issue)),
    )
