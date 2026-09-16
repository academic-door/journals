from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup


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
