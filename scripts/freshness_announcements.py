from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from urllib.parse import urlparse


ALLOWED_SOURCE_KINDS = {
    "official_current",
    "official_archive",
    "official_rss",
    "association_announcement",
    "official_newsletter",
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _valid_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _valid_observed_at(value: str) -> bool:
    if not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def normalize_announcement(
    journal_id: str,
    raw: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate a first-party issue announcement without promoting its roster.

    Announcements assert only issue existence/identity and provenance. They never
    assert article membership, abstracts, translation completeness, or canonical
    publication readiness.
    """

    if not isinstance(raw, dict):
        return None
    if _text(raw.get("source_authority")) != "first_party":
        return None
    source_kind = _text(raw.get("source_kind"))
    if source_kind not in ALLOWED_SOURCE_KINDS:
        return None
    source_url = _text(raw.get("source_url"))
    observed_at = _text(raw.get("observed_at"))
    issue_id = _text(raw.get("issue_id"))
    volume = _text(raw.get("volume"))
    issue = _text(raw.get("issue"))
    publication_date = _text(raw.get("publication_date"))
    if not all((journal_id, issue_id, volume, issue, publication_date)):
        return None
    if not _valid_https_url(source_url) or not _valid_observed_at(observed_at):
        return None

    issue_label = _text(raw.get("issue_label"))
    if not issue_label:
        issue_label = f"Vol. {volume} · No. {issue}"
    return {
        "schema_version": "1.0",
        "journal_id": journal_id,
        "issue_id": issue_id,
        "volume": volume,
        "issue": issue,
        "issue_label": issue_label,
        "publication_date": publication_date,
        "publication_state": "announced",
        "source_authority": "first_party",
        "source_kind": source_kind,
        "source_url": source_url,
        "observed_at": observed_at,
    }


def _numeric(value: str) -> int:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else -1


def _period_key(value: str) -> tuple[int, int]:
    text = value.strip()
    iso = re.match(r"^(20\d{2})-(\d{2})", text)
    if iso:
        return int(iso.group(1)), int(iso.group(2))
    named = re.match(
        r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})$",
        text,
        re.IGNORECASE,
    )
    if named:
        return int(named.group(2)), MONTHS[named.group(1).casefold()]
    year = re.search(r"(20\d{2})", text)
    return (int(year.group(1)), 0) if year else (0, 0)


def _issue_order_key(value: dict[str, Any] | None) -> tuple[int, int, int, int]:
    if not value:
        return (-1, -1, -1, -1)
    volume = _numeric(_text(value.get("volume")))
    issue = _numeric(_text(value.get("issue")))
    year, month = _period_key(_text(value.get("publication_date")))
    # Volume/issue identity wins when numeric. Period breaks ties and handles
    # continuous-volume journals whose issue token is non-numeric (for example c).
    return (volume, issue, year, month)


def _best_content(
    ready: dict[str, Any] | None,
    detected: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not ready:
        return detected
    if not detected:
        return ready
    if _text(ready.get("issue_id")) == _text(detected.get("issue_id")):
        return ready
    return detected if _issue_order_key(detected) > _issue_order_key(ready) else ready


def announcement_is_newer(
    announcement: dict[str, Any] | None,
    ready: dict[str, Any] | None,
    detected: dict[str, Any] | None,
) -> bool:
    """True only when an announcement is ahead of every reader content layer."""

    if not announcement:
        return False
    content = _best_content(ready, detected)
    if not content:
        return True
    if _text(announcement.get("issue_id")) == _text(content.get("issue_id")):
        return False
    return _issue_order_key(announcement) > _issue_order_key(content)


def announcement_entry_fields(
    journal_id: str,
    raw: dict[str, Any] | None,
    *,
    ready: dict[str, Any] | None,
    detected: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return additive collection fields for one validated announcement."""

    item = normalize_announcement(journal_id, raw)
    if not item:
        return {}
    return {
        "latest_announced_issue_id": item["issue_id"],
        "latest_announced_issue_label": item["issue_label"],
        "latest_announced_publication_date": item["publication_date"],
        "latest_announced_source_kind": item["source_kind"],
        "latest_announced_source_url": item["source_url"],
        "latest_announced_observed_at": item["observed_at"],
        "latest_announced_is_newer": announcement_is_newer(item, ready, detected),
    }
