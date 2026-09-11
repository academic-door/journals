"""Deterministic precedence for overlapping historical state observations.

Discovery authority is a hard fence.  Candidate/configured evidence can never
replace publisher-authoritative discovery merely because it was written later.
Issue-level readers may then prefer a more complete exact reference before
comparing actual observation freshness.  Whole journal snapshots preserve the
measurement window: within one authority class, a demonstrably fresher snapshot
wins, with completeness used only when freshness does not decide the result.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


AUTHORITATIVE_AUTHORITIES = {
    "official_archive",
    "official_archive_snapshot",
    "official-issue-page",
    "official_issue_page",
    "publisher_verified",
    "publisher_archive",
    "publisher_issue_page",
}
CANDIDATE_AUTHORITIES = {
    "crossref_candidate",
    "configured_schedule_candidate",
    "",
}

_PUBLICATION_RANK = {
    "ready": 4,
    "complete": 4,
    "translation_partial": 3,
    "source_pending": 2,
    "enriching": 1,
    "collected": 1,
    "blocked": 0,
}
_GENERIC_ROUTE_TAILS = {
    "archive",
    "archives",
    "issue",
    "issues",
    "toc",
    "volumes",
    "volumes-and-issues",
}


def _stable(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def authority_rank(authority: object) -> int:
    value = str(authority or "")
    if value in AUTHORITATIVE_AUTHORITIES:
        return 2
    if value in CANDIDATE_AUTHORITIES:
        return 1
    return 0


def official_url_specificity(url: object) -> int:
    """Return 0 missing, 1 collection/root, 2 concrete route."""

    value = str(url or "").strip()
    if not value:
        return 0
    try:
        parsed = urlparse(value)
    except ValueError:
        return 0
    if parsed.query or parsed.fragment:
        return 2
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    if not parts:
        return 1
    return 1 if parts[-1] in _GENERIC_ROUTE_TAILS else 2


def reference_rank(reference: object) -> tuple[int, int, int, int, int]:
    if not isinstance(reference, dict):
        return (0, 0, 0, 0, 0)
    return (
        official_url_specificity(reference.get("official_url")),
        int(bool(reference.get("volume"))),
        int(bool(reference.get("issue"))),
        int(bool(reference.get("year"))),
        int(bool(reference.get("journal"))),
    )


def choose_reference(existing: object, incoming: object) -> dict[str, Any]:
    old = existing if isinstance(existing, dict) else {}
    new = incoming if isinstance(incoming, dict) else {}
    old_rank = reference_rank(old)
    new_rank = reference_rank(new)
    if new_rank != old_rank:
        return dict(new if new_rank > old_rank else old)
    return dict(new if _stable(new) > _stable(old) else old)


def choose_issue_expectation(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Choose one issue observation independent of shard iteration order."""

    if not existing:
        return dict(incoming)
    old_authority = authority_rank(existing.get("authority"))
    new_authority = authority_rank(incoming.get("authority"))
    if new_authority != old_authority:
        return dict(incoming if new_authority > old_authority else existing)

    old_ref = reference_rank(existing)
    new_ref = reference_rank(incoming)
    if new_ref != old_ref:
        return dict(incoming if new_ref > old_ref else existing)

    old_stamp = _timestamp(existing.get("refreshed_at"))
    new_stamp = _timestamp(incoming.get("refreshed_at"))
    if old_stamp is not None and new_stamp is not None and old_stamp != new_stamp:
        return dict(incoming if new_stamp > old_stamp else existing)
    return dict(incoming if _stable(incoming) > _stable(existing) else existing)


def _snapshot_reference_score(snapshot: dict[str, Any]) -> tuple[int, int, int]:
    refs = snapshot.get("issue_refs")
    if not isinstance(refs, dict):
        return (0, 0, 0)
    ranks = [reference_rank(value) for value in refs.values() if isinstance(value, dict)]
    return (
        sum(rank[0] == 2 for rank in ranks),
        sum(rank[0] > 0 for rank in ranks),
        sum(sum(rank[1:]) for rank in ranks),
    )


def choose_discovery_snapshot(existing: object, incoming: object) -> object:
    """Choose a whole journal discovery snapshot without inventing freshness."""

    if not isinstance(existing, dict):
        return incoming
    if not isinstance(incoming, dict):
        return existing

    old_authority = authority_rank(existing.get("authority"))
    new_authority = authority_rank(incoming.get("authority"))
    if new_authority != old_authority:
        return incoming if new_authority > old_authority else existing

    old_stamp = _timestamp(existing.get("refreshed_at") or existing.get("updated_at"))
    new_stamp = _timestamp(incoming.get("refreshed_at") or incoming.get("updated_at"))
    if old_stamp is not None and new_stamp is not None and old_stamp != new_stamp:
        return incoming if new_stamp > old_stamp else existing

    old_score = _snapshot_reference_score(existing)
    new_score = _snapshot_reference_score(incoming)
    if new_score != old_score:
        return incoming if new_score > old_score else existing
    return incoming if _stable(incoming) > _stable(existing) else existing


def _issue_rank(payload: dict[str, Any]) -> tuple[int, int, int]:
    publication_state = payload.get("publication_state") or payload.get("status") or "blocked"
    return (
        _PUBLICATION_RANK.get(str(publication_state), 0),
        int(payload.get("content_status") == "complete"),
        int(payload.get("source_status") in {"official_verified", "publisher_verified"}),
    )


def choose_issue_entry(existing: object, incoming: object) -> object:
    """Preserve stronger issue state, then actual attempt freshness, deterministically."""

    if not isinstance(existing, dict):
        return incoming
    if not isinstance(incoming, dict):
        return existing
    old_rank = _issue_rank(existing)
    new_rank = _issue_rank(incoming)
    if new_rank != old_rank:
        return incoming if new_rank > old_rank else existing

    old_stamp = _timestamp(existing.get("last_attempt_at") or existing.get("updated_at"))
    new_stamp = _timestamp(incoming.get("last_attempt_at") or incoming.get("updated_at"))
    if old_stamp is not None and new_stamp is not None and old_stamp != new_stamp:
        return incoming if new_stamp > old_stamp else existing
    if old_stamp is not None and new_stamp is None:
        return existing
    if new_stamp is not None and old_stamp is None:
        return incoming
    return incoming if _stable(incoming) > _stable(existing) else existing
