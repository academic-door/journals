"""Backfill validated historical issues without changing the current snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.history import (
    HistoricalIssue,
    discover_official_issues,
    historical_issue_sort_key,
)
from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    JOURNALS_PATH,
    PUBLIC_API,
    TRANSLATION_CACHE,
    apply_translation_cache,
    archive_issue,
    issue_content_status,
    issue_publication_state,
    issue_source_status,
    load_available_issues,
    normalize_issue_content,
    now_iso,
    stamp_issue_readiness,
    update_indexes,
    validate_issue,
    write_archive_index,
)


HISTORY_CONFIG = ROOT / "config" / "top5-history.yml"
STATE_PATH = ROOT / "data" / "backfill-state" / "top5-2025-2026.json"
STAGING_ROOT = ROOT / "data" / "backfill-staging"
STATE_SCHEMA_VERSION = "1.1"
COLLECTOR_REVISION = "history-integrity-2026-08-11"
DISCOVERY_MAX_AGE = timedelta(days=14)

VERIFIED_SOURCE_STATUSES = {"official_verified", "publisher_verified"}
READY_STATUSES = {"ready"}
LEGACY_READY_STATUSES = {"complete"}


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_json(path: Path, default: object) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def collector_for_issue(
    journal_config: dict[str, Any], issue_ref: HistoricalIssue | str
) -> Callable[[], dict[str, Any]]:
    collector = journal_config["collector"]
    issue_url = (
        issue_ref.official_url if isinstance(issue_ref, HistoricalIssue) else str(issue_ref)
    )

    def _require_exact_issue(candidate: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(issue_ref, HistoricalIssue):
            return candidate
        received_volume = str(candidate.get("volume", "")).casefold()
        received_issue = str(candidate.get("issue", "")).casefold()
        if (
            received_volume != str(issue_ref.volume).casefold()
            or received_issue != str(issue_ref.issue).casefold()
        ):
            raise ValueError(
                f"Official page mismatch: expected {issue_ref.volume}/{issue_ref.issue}, "
                f"received {candidate.get('volume')}/{candidate.get('issue')}"
            )
        return candidate

    def _crossref() -> dict[str, Any]:
        from collectors.metadata_fallback import fetch_crossref_current_issue

        if not isinstance(issue_ref, HistoricalIssue):
            raise ValueError("Crossref history requires a volume/issue reference")
        candidate = fetch_crossref_current_issue(
            journal_id=journal_config["id"],
            journal_name=journal_config["name"],
            issn=str(journal_config["issn"]),
            current_issue_url=issue_url,
            target_volume=issue_ref.volume,
            target_issue=(
                issue_ref.issue if issue_ref.issue.casefold() != "c" else ""
            ),
            output_issue=issue_ref.issue.upper(),
            start_year=int(issue_ref.year) - 2,
        )
        quality = candidate.setdefault("quality", {})
        flags = quality.setdefault("flags", [])
        for flag in (
            "publisher_html_blocked_crossref_fallback",
            "crossref_provisional_roster",
        ):
            if flag not in flags:
                flags.append(flag)
        quality["roster_authority"] = "crossref-provisional"
        quality.setdefault("roster_transport", "crossref")
        candidate["source_status"] = "source_pending"
        candidate["publication_state"] = "source_pending"
        return candidate

    if collector == "crossref":
        # Crossref is discovery/metadata support only.  It can be archived for
        # review, but the explicit provisional markers prevent promotion.
        return _crossref

    if collector == "aea":
        from collectors.aea import fetch_current_issue

        def _collect_aea() -> dict[str, Any]:
            try:
                return _require_exact_issue(
                    fetch_current_issue(
                        issue_url,
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                    )
                )
            except Exception:
                if (
                    isinstance(issue_ref, HistoricalIssue)
                    and journal_config.get("fallback") == "crossref"
                ):
                    return _crossref()
                raise

        return _collect_aea
    if collector == "chicago":
        from collectors.chicago import fetch_current_issue

        def _collect_chicago() -> dict[str, Any]:
            try:
                # JPE must always try the exact official issue page first.
                return _require_exact_issue(
                    fetch_current_issue(
                        issue_url,
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                    )
                )
            except Exception:
                if not isinstance(issue_ref, HistoricalIssue):
                    raise
                if journal_config.get("fallback") == "crossref-repec":
                    from collectors.metadata_fallback import fetch_repec_history_issue

                    try:
                        return fetch_repec_history_issue(
                            journal_id=journal_config["id"],
                            journal_name=journal_config["name"],
                            issn=str(journal_config["issn"]),
                            volume=issue_ref.volume,
                            issue=issue_ref.issue,
                            repec_series_code=journal_config.get(
                                "repec_series_code", "ucp/jpolec"
                            ),
                        )
                    except Exception:
                        return _crossref()
                raise

        return _collect_chicago
    if collector == "oup":
        from collectors.oup import fetch_current_issue

        def _collect_oup() -> dict[str, Any]:
            try:
                return _require_exact_issue(
                    fetch_current_issue(journal_config["id"], issue_url)
                )
            except Exception:
                if (
                    isinstance(issue_ref, HistoricalIssue)
                    and journal_config.get("fallback") == "crossref"
                ):
                    return _crossref()
                raise

        return _collect_oup
    if collector in ("wiley", "repec"):
        from collectors.wiley import fetch_current_issue

        def _collect_wiley() -> dict[str, Any]:
            official_error: Exception | None = None
            if collector == "wiley":
                try:
                    return _require_exact_issue(
                        fetch_current_issue(
                            issue_url,
                            journal_id=journal_config["id"],
                            journal_name=journal_config["name"],
                            expected_volume=(
                                issue_ref.volume
                                if isinstance(issue_ref, HistoricalIssue)
                                else ""
                            ),
                            expected_issue=(
                                issue_ref.issue
                                if isinstance(issue_ref, HistoricalIssue)
                                else ""
                            ),
                        )
                    )
                except Exception as error:
                    official_error = error
            if isinstance(issue_ref, HistoricalIssue) and journal_config.get(
                "repec_series_code"
            ):
                from collectors.metadata_fallback import fetch_repec_history_issue

                try:
                    return fetch_repec_history_issue(
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                        issn=str(journal_config["issn"]),
                        volume=issue_ref.volume,
                        issue=issue_ref.issue,
                        repec_series_code=journal_config["repec_series_code"],
                    )
                except Exception:
                    if journal_config.get("fallback") == "crossref":
                        return _crossref()
                    raise
            if official_error is not None:
                if (
                    isinstance(issue_ref, HistoricalIssue)
                    and journal_config.get("fallback") == "crossref"
                ):
                    return _crossref()
                raise official_error
            raise ValueError("RePEc history requires a configured series code")

        return _collect_wiley
    if collector == "elsevier":
        if not isinstance(issue_ref, HistoricalIssue):
            raise ValueError("Elsevier history requires a volume/issue reference")
        from collectors.elsevier import fetch_elsevier_repec_history_issue
        from collectors.metadata_fallback import fetch_crossref_current_issue

        def _collect() -> dict[str, Any]:
            from collectors.metadata_fallback import fetch_elsevier_issue_via_search

            try:
                # The ScienceDirect API is the publisher-owned roster.
                return _require_exact_issue(
                    fetch_elsevier_issue_via_search(
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                        issn=str(journal_config["issn"]),
                        volume=issue_ref.volume,
                        issue=issue_ref.issue,
                        official_issue_url=issue_url,
                    )
                )
            except Exception:
                try:
                    return fetch_elsevier_repec_history_issue(
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                        issn=str(journal_config["issn"]),
                        volume=issue_ref.volume,
                        issue=issue_ref.issue,
                        repec_series_url=journal_config.get("repec_series_url", ""),
                        doi_template=journal_config.get("doi_template", ""),
                    )
                except Exception:
                    return _crossref()

        return _collect
    raise ValueError(f"Historical backfill is not configured for {collector}")


def staging_path(issue: HistoricalIssue) -> Path:
    return STAGING_ROOT / issue.journal.casefold() / f"{issue.issue_id}.json"


MANUAL_RETRY_CLASSES = {"manual", "missing_abstract", "in_progress"}
TRANSIENT_RETRY_CLASSES = {"transient", "translation", "source"}


def retry_class_for(status: str, error: str = "") -> str:
    """Classify a checkpoint so scheduled runs stop hammering unactionable work.

    - translation: partial translations that should be retried after a short
      backoff with the current model.
    - transient: HTTP/timeout failures that may resolve on the next attempt.
    - in_progress: the publisher has not finalised the volume yet; wait for
      the regular monitor instead of re-collecting every hour.
    - missing_abstract: the source has no English abstract; requires an
      enrichment source or a manual/browser-authorized capture.
    - manual: anything else needs an operator or a different tool.
    """
    if status == "translation_partial":
        return "translation"
    if status == "source_pending":
        return "source"
    if status == "collected":
        return "transient"
    error_text = str(error or "").lower()
    if "archive_missing" in error_text:
        return "transient"
    if "possible_incomplete_volume" in error_text:
        return "manual"
    if any(
        marker in error_text
        for marker in (
            "in progress",
            "awaiting",
            "not yet published",
            "not finalised",
            "not finalized",
            "publisher has not",
        )
    ):
        return "in_progress"
    if "abstract" in error_text and any(
        marker in error_text for marker in ("missing", "unavailable", "no abstract")
    ):
        return "missing_abstract"
    if any(
        marker in error_text
        for marker in (
            "timeout",
            "http 429",
            "http 5",
            "http 4",
            "connection",
            "temporarily",
            "rate limit",
            "retry",
        )
    ):
        return "transient"
    return "manual"


def next_retry_after(retry_class: str, now) -> str | None:
    """Backoff window for retryable classes; manual classes get no retry."""
    if retry_class not in TRANSIENT_RETRY_CLASSES:
        return None
    hours = 24 if retry_class == "source" else (6 if retry_class == "translation" else 2)

    return (now + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def source_status_for_issue(issue: dict[str, Any]) -> str:
    """Infer source authority conservatively for legacy archive JSON."""

    return issue_source_status(issue)


def issue_integrity(issue: dict[str, Any]) -> dict[str, Any]:
    """Return content/source/publication truth without trusting legacy labels."""

    if not isinstance(issue, dict):
        return {
            "archive_valid": False,
            "content_status": "blocked",
            "source_status": "source_pending",
            "publication_state": "blocked",
            "reason": "archive_not_object",
        }
    try:
        stamp_issue_readiness(issue)
        content_status = issue_content_status(issue)
        source_status = issue_source_status(issue)
        publication_state = issue_publication_state(issue)
    except Exception as error:
        return {
            "archive_valid": False,
            "content_status": "blocked",
            "source_status": "source_pending",
            "publication_state": "blocked",
            "reason": f"archive_content_gate_error: {type(error).__name__}: {error}",
        }
    archive_valid = content_status != "blocked"
    reason = (
        "archive_content_gate_failed"
        if content_status == "blocked"
        else ("translation_incomplete" if content_status == "translation_partial" else "")
    )
    return {
        "archive_valid": archive_valid,
        "content_status": content_status,
        "source_status": source_status,
        "publication_state": publication_state,
        "reason": reason,
    }


def inspect_archive(
    path: Path,
    *,
    expected_issue_id: str = "",
    expected_journal_id: str = "",
    write_back: bool = False,
) -> dict[str, Any]:
    """Read, normalize and gate an archive; existence alone is never success."""

    if not path.exists():
        return {
            "archive_exists": False,
            "archive_valid": False,
            "content_status": "blocked",
            "source_status": "source_pending",
            "publication_state": "blocked",
            "reason": "archive_missing",
        }
    try:
        original = load_json(path, {})
        normalized = normalize_issue_content(
            json.loads(json.dumps(original, ensure_ascii=False))
        )
        validate_issue(normalized)
    except Exception as error:
        return {
            "archive_exists": True,
            "archive_valid": False,
            "content_status": "blocked",
            "source_status": "source_pending",
            "publication_state": "blocked",
            "reason": f"archive_invalid: {type(error).__name__}: {error}",
        }
    integrity = issue_integrity(normalized)
    if expected_issue_id and normalized.get("issue_id") != expected_issue_id:
        integrity.update(
            archive_valid=False,
            content_status="blocked",
            publication_state="blocked",
            reason=(
                f"archive_issue_id_mismatch: expected {expected_issue_id}, "
                f"got {normalized.get('issue_id', '')}"
            ),
        )
    if expected_journal_id and normalized.get("journal_id") != expected_journal_id:
        integrity.update(
            archive_valid=False,
            content_status="blocked",
            publication_state="blocked",
            reason=(
                f"archive_journal_id_mismatch: expected {expected_journal_id}, "
                f"got {normalized.get('journal_id', '')}"
            ),
        )
    normalized.update(
        content_status=integrity["content_status"],
        source_status=integrity["source_status"],
        publication_state=integrity["publication_state"],
    )
    if write_back and normalized != original:
        atomic_write_json(path, normalized)
    return {**integrity, "archive_exists": True, "issue": normalized}


def _status_fields(status: str, integrity: dict[str, Any] | None) -> dict[str, str]:
    if integrity:
        return {
            "content_status": str(integrity.get("content_status", "blocked")),
            "source_status": str(integrity.get("source_status", "source_pending")),
            "publication_state": str(integrity.get("publication_state", status)),
        }
    if status == "ready":
        return {
            "content_status": "complete",
            "source_status": "official_verified",
            "publication_state": "ready",
        }
    if status == "source_pending":
        return {
            "content_status": "complete",
            "source_status": "source_pending",
            "publication_state": "source_pending",
        }
    if status == "translation_partial":
        return {
            "content_status": "translation_partial",
            "source_status": "source_pending",
            "publication_state": "translation_partial",
        }
    return {
        "content_status": "blocked",
        "source_status": "source_pending",
        "publication_state": "blocked",
    }


def update_state(
    state: dict[str, Any],
    issue: HistoricalIssue,
    *,
    status: str,
    error: str = "",
    integrity: dict[str, Any] | None = None,
) -> None:
    retry_class = retry_class_for(status, error)
    entry = {
        "journal": issue.journal,
        "year": issue.year,
        "volume": issue.volume,
        "issue": issue.issue,
        "official_url": issue.official_url,
        "status": status,
        "last_error": error,
        "retry_class": retry_class,
        **_status_fields(status, integrity),
    }
    next_at = next_retry_after(retry_class, datetime.now(timezone.utc))
    if next_at:
        entry["next_retry_at"] = next_at
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    state.setdefault("issues", {})[issue.issue_id] = entry
    atomic_write_json(STATE_PATH, state)


def is_actionable(entry: dict[str, Any] | None) -> bool:
    """Whether a checkpoint should be retried by the scheduled backfill."""
    if not entry:
        return True
    status = entry.get("status", "")
    if status in READY_STATUSES | LEGACY_READY_STATUSES:
        return False
    retry_class = entry.get("retry_class") or retry_class_for(
        status, entry.get("last_error", "")
    )
    if retry_class in MANUAL_RETRY_CLASSES:
        return False
    next_at = entry.get("next_retry_at")
    if next_at:
        from datetime import datetime, timezone

        try:
            parsed = datetime.fromisoformat(str(next_at))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed > datetime.now(timezone.utc):
                return False
        except ValueError:
            pass
    return True


def discovery_authority(definition: dict[str, Any]) -> str:
    return (
        "crossref_candidate"
        if str(definition.get("platform", "")).casefold() == "crossref"
        else "official_archive"
    )


def record_discovery(
    state: dict[str, Any],
    journal: str,
    issues: list[HistoricalIssue],
    definition: dict[str, Any],
    *,
    refreshed_at: str | None = None,
) -> None:
    """Persist the authoritative discovery snapshot independently of state."""

    ordered = sorted(issues, key=historical_issue_sort_key)
    stamp = refreshed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["updated_at"] = stamp
    state.setdefault("discovery", {})[journal] = {
        "issue_ids": [issue.issue_id for issue in ordered],
        "issue_years": {issue.issue_id: issue.year for issue in ordered},
        "issue_refs": {
            issue.issue_id: {
                "journal": issue.journal,
                "year": issue.year,
                "volume": issue.volume,
                "issue": issue.issue,
                "official_url": issue.official_url,
            }
            for issue in ordered
        },
        "authority": discovery_authority(definition),
        "refreshed_at": stamp,
        "collector_revision": COLLECTOR_REVISION,
    }
    atomic_write_json(STATE_PATH, state)


def plan_from_discovery(
    state: dict[str, Any], journals: list[str], years: range
) -> list[HistoricalIssue]:
    """Render a read-only plan from the last persisted discovery snapshot."""

    wanted_years = set(years)
    result: list[HistoricalIssue] = []
    for journal in journals:
        snapshot = state.get("discovery", {}).get(journal, {}) or {}
        refs = snapshot.get("issue_refs", {}) or {}
        for issue_id in snapshot.get("issue_ids", []):
            raw = refs.get(issue_id) or state.get("issues", {}).get(issue_id, {})
            try:
                item = HistoricalIssue(
                    journal=str(raw.get("journal") or journal),
                    year=int(raw["year"]),
                    volume=str(raw["volume"]),
                    issue=str(raw.get("issue") or str(issue_id).rsplit("-", 1)[-1]),
                    official_url=str(raw["official_url"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if item.year in wanted_years:
                result.append(item)
    return sorted(result, key=historical_issue_sort_key)


def migrate_legacy_state(
    state: dict[str, Any],
    journals: dict[str, Any],
    *,
    public_api: Path = PUBLIC_API,
) -> bool:
    """Upgrade 1.0 checkpoints and re-gate every claimed archive in place."""

    changed = str(state.get("schema_version", "1.0")) != STATE_SCHEMA_VERSION
    state["schema_version"] = STATE_SCHEMA_VERSION
    for issue_id, entry in state.setdefault("issues", {}).items():
        status = str(entry.get("status", "") or "")
        if status not in {
            "complete",
            "ready",
            "source_pending",
            "translation_partial",
        }:
            continue
        journal_key = str(entry.get("journal", ""))
        config = journals.get(journal_key) or journals.get(journal_key.upper())
        if not config:
            continue
        archive = (
            public_api
            / "journals"
            / str(config["id"])
            / "issues"
            / f"{issue_id}.json"
        )
        integrity = inspect_archive(
            archive,
            expected_issue_id=issue_id,
            expected_journal_id=str(config["id"]),
            write_back=True,
        )
        migrated_status = str(integrity["publication_state"])
        next_error = str(integrity.get("reason", "")) if migrated_status == "blocked" else ""
        next_retry = retry_class_for(migrated_status, next_error)
        desired = {
            "status": migrated_status,
            "last_error": next_error,
            "retry_class": next_retry,
            **_status_fields(migrated_status, integrity),
        }
        if any(entry.get(key) != value for key, value in desired.items()):
            entry.update(desired)
            # A legacy `complete` claim that is downgraded must be retried in
            # this very run; the normal backoff is written after that attempt.
            entry.pop("next_retry_at", None)
            changed = True
    if changed:
        state["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return changed


def _is_browser_captured_staging(path: Path) -> bool:
    """A browser-authorized snapshot is authoritative for the official roster;
    refreshing would replace it with an incomplete RePEc/Crossref list."""
    if not path.exists():
        return False
    try:
        issue = load_json(path, {})
    except Exception:
        return False
    quality = issue.get("quality", {}) or {}
    if quality.get("browser_capture") or quality.get(
        "browser_authorized_abstracts"
    ):
        return True
    roster = str(quality.get("roster_transport", "")).lower()
    return "browser" in roster or "browser-authorized" in roster


def collect_or_resume(
    issue_ref: HistoricalIssue,
    journal_config: dict[str, Any],
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    path = staging_path(issue_ref)
    if path.exists() and (not refresh or _is_browser_captured_staging(path)):
        return load_json(path, {})
    issue = collector_for_issue(journal_config, issue_ref)()
    if (
        str(issue.get("volume", "")).casefold() != str(issue_ref.volume).casefold()
        or str(issue.get("issue", "")).casefold()
        != str(issue_ref.issue).casefold()
    ):
        raise ValueError(
            f"Official page mismatch: expected {issue_ref.volume}/{issue_ref.issue}, "
            f"received {issue.get('volume')}/{issue.get('issue')}"
        )
    issue = normalize_issue_content(apply_translation_cache(issue))
    validate_issue(issue)
    atomic_write_json(path, issue)
    return issue


def history_completeness_block(
    issue: dict[str, Any],
    journal_config: dict[str, Any],
    public_api: Path = PUBLIC_API,
) -> str:
    """Return a block reason when a history volume looks implausibly small.

    Elsevier continuous volumes are sometimes thin in Crossref/OpenAlex. Never
    publish a historical issue that is under half the journal's current issue
    size; keep it staged for a manual or browser-authorized capture instead.
    """

    quality = issue.get("quality") or {}
    if quality.get("browser_capture") or "browser-authorized" in str(
        quality.get("roster_transport", "")
    ):
        # A browser-authorized snapshot is the official publisher roster;
        # comparing it against the current issue size would wrongly block
        # genuinely small historical volumes.
        return ""

    collected_count = int(issue.get("research_article_count", 0))
    repec_count = int(quality.get("repec_item_count", 0))
    if repec_count >= 5:
        # RePEc mirrors the publisher's per-volume list, so a volume that
        # collected about as many articles as RePEc lists is genuinely that
        # size (JDE 173 is a real ~12-article issue, not a data gap).
        if collected_count < repec_count * 0.8:
            return (
                f"possible_incomplete_volume: {collected_count} articles "
                f"collected vs RePEc volume {repec_count}; "
                "needs official page or browser-authorized capture"
            )
        return ""
    current_path = (
        public_api / "journals" / journal_config["id"] / "issues" / "current.json"
    )
    reference_count = 0
    if current_path.exists():
        try:
            reference_count = int(
                load_json(current_path, {}).get("research_article_count", 0)
            )
        except (TypeError, ValueError):
            reference_count = 0
    if reference_count >= 10 and collected_count < reference_count * 0.5:
        return (
            f"possible_incomplete_volume: {collected_count} articles "
            f"collected vs current issue {reference_count}; "
            "needs official page or browser-authorized capture"
        )
    return ""


def run_issue(
    issue_ref: HistoricalIssue,
    journal_config: dict[str, Any],
    state: dict[str, Any],
    *,
    translate: bool,
    max_translations: int,
) -> dict[str, Any]:
    current_status = state.get("issues", {}).get(issue_ref.issue_id, {}).get("status")
    current_error = (
        state.get("issues", {}).get(issue_ref.issue_id, {}).get("last_error", "")
    )
    entry = state.get("issues", {}).get(issue_ref.issue_id)
    archive_path = (
        PUBLIC_API
        / "journals"
        / journal_config["id"]
        / "issues"
        / f"{issue_ref.issue_id}.json"
    )
    if archive_path.exists():
        archive_integrity = inspect_archive(
            archive_path,
            expected_issue_id=issue_ref.issue_id,
            expected_journal_id=str(journal_config["id"]),
            write_back=True,
        )
        if archive_integrity.get("publication_state") == "ready":
            update_state(
                state,
                issue_ref,
                status="ready",
                integrity=archive_integrity,
            )
            return {"issue_id": issue_ref.issue_id, "result": "already_ready"}
        if current_status in LEGACY_READY_STATUSES | READY_STATUSES:
            # A stale ready/complete marker may hide a provisional or
            # incomplete archive. Recollect it now instead of trusting state
            # over the archive read-back gate.
            entry = {
                **(entry or {}),
                "status": archive_integrity["publication_state"],
                "retry_class": "",
                "next_retry_at": "",
            }
            current_status = str(archive_integrity["publication_state"])
            current_error = str(archive_integrity.get("reason", ""))
    if not is_actionable(entry):
        retry_class = (entry or {}).get("retry_class") or retry_class_for(
            str(current_status or ""), current_error
        )
        return {
            "issue_id": issue_ref.issue_id,
            "result": "blocked",
            "error": (
                f"{retry_class} (skipped: not actionable by the scheduled "
                "backfill)"
            ),
        }
    try:
        issue = collect_or_resume(
            issue_ref,
            journal_config,
            refresh=current_status not in ("ready", "translation_partial"),
        )
        update_state(state, issue_ref, status="collected")
        # Fill missing English abstracts/authors before translating so
        # articles that only had a Crossref roster can still be translated.
        from collectors.metadata_fallback import enrich_missing_metadata

        issue = enrich_missing_metadata(issue)
        issue = apply_translation_cache(issue)
        translation_report = None
        if translate:
            translation_report = translate_missing(
                issue,
                TRANSLATION_CACHE / f"{journal_config['id']}.json",
                max_translations=max_translations,
            )
            issue = apply_translation_cache(issue)
        issue = normalize_issue_content(issue)
        validate_issue(issue)
        atomic_write_json(staging_path(issue_ref), issue)
        integrity = issue_integrity(issue)
        if integrity["content_status"] == "translation_partial":
            issue.update(
                content_status=integrity["content_status"],
                source_status=integrity["source_status"],
                publication_state=integrity["publication_state"],
            )
            atomic_write_json(staging_path(issue_ref), issue)
            update_state(
                state,
                issue_ref,
                status="translation_partial",
                integrity=integrity,
            )
            return {
                "issue_id": issue_ref.issue_id,
                "result": "translation_partial",
                "translation": translation_report,
            }
        if integrity["content_status"] == "blocked":
            reason = str(integrity.get("reason", "archive_content_gate_failed"))
            update_state(
                state,
                issue_ref,
                status="blocked",
                error=reason,
                integrity=integrity,
            )
            return {
                "issue_id": issue_ref.issue_id,
                "result": "blocked",
                "error": reason,
            }
        block_reason = history_completeness_block(issue, journal_config)
        if block_reason:
            update_state(state, issue_ref, status="blocked", error=block_reason)
            return {
                "issue_id": issue_ref.issue_id,
                "result": "blocked",
                "error": "possible_incomplete_volume",
            }
        issue.update(
            content_status=integrity["content_status"],
            source_status=integrity["source_status"],
            publication_state=integrity["publication_state"],
        )
        atomic_write_json(staging_path(issue_ref), issue)
        target = archive_issue(issue, replace_non_ready=True)
        if target is None:
            raise ValueError("Historical issue failed archive publication gate")
        readback_integrity = inspect_archive(
            target,
            expected_issue_id=issue_ref.issue_id,
            expected_journal_id=str(journal_config["id"]),
            write_back=True,
        )
        final_status = str(readback_integrity["publication_state"])
        if final_status not in {"ready", "source_pending"}:
            raise ValueError(
                "Historical issue failed archive read-back gate: "
                + str(readback_integrity.get("reason", final_status))
            )
        update_state(
            state,
            issue_ref,
            status=final_status,
            error=(
                "source authority pending official verification"
                if final_status == "source_pending"
                else ""
            ),
            integrity=readback_integrity,
        )
        return {
            "issue_id": issue_ref.issue_id,
            "result": final_status,
            "articles": issue["research_article_count"],
            "translation": translation_report,
        }
    except Exception as error:
        update_state(
            state,
            issue_ref,
            status="blocked",
            error=f"{type(error).__name__}: {error}",
        )
        return {
            "issue_id": issue_ref.issue_id,
            "result": "blocked",
            "error": f"{type(error).__name__}: {error}",
        }


def rotate_journals(
    state: dict[str, Any],
    history: dict[str, Any],
    *,
    years: range,
    max_journals: int,
    journals: dict[str, Any] | None = None,
    public_api: Path = PUBLIC_API,
    now: datetime | None = None,
) -> list[str]:
    """Pick up to max_journals journals with actionable pending work.

    Works purely from the resumable state file (no publisher API calls), so
    the workflow's pending-check step stays cheap even when nothing is due.
    Journals that have never run (no checkpoints in the requested years) are
    always eligible so the first batch can discover and collect them. Eligible
    journals are ordered by their last run timestamp (oldest first).
    """
    wanted_years = set(years)
    current_time = now or datetime.now(timezone.utc)
    candidates: set[str] = set()
    for key in history["journals"]:
        snapshot = state.get("discovery", {}).get(key)
        if not isinstance(snapshot, dict):
            candidates.add(key)
            continue
        try:
            refreshed = datetime.fromisoformat(str(snapshot.get("refreshed_at", "")))
            if refreshed.tzinfo is None:
                refreshed = refreshed.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            candidates.add(key)
            continue
        if (
            current_time - refreshed >= DISCOVERY_MAX_AGE
            or snapshot.get("collector_revision") != COLLECTOR_REVISION
        ):
            candidates.add(key)
            continue
        issue_years = snapshot.get("issue_years", {}) or {}
        issue_ids = [
            str(issue_id)
            for issue_id in snapshot.get("issue_ids", [])
            if not issue_years or issue_years.get(issue_id) in wanted_years
        ]
        for issue_id in issue_ids:
            entry = state.get("issues", {}).get(issue_id)
            if not entry:
                candidates.add(key)
                break
            if is_actionable(entry):
                candidates.add(key)
                break
            if (
                journals
                and str(entry.get("status", "")) in READY_STATUSES | LEGACY_READY_STATUSES
            ):
                config = journals.get(key)
                if config:
                    archive = (
                        public_api
                        / "journals"
                        / str(config["id"])
                        / "issues"
                        / f"{issue_id}.json"
                    )
                    integrity = inspect_archive(
                        archive,
                        expected_issue_id=issue_id,
                        expected_journal_id=str(config["id"]),
                    )
                    if integrity.get("publication_state") != "ready":
                        candidates.add(key)
                        break
    if not candidates:
        return []
    last_run: dict[str, str] = state.setdefault("rotation", {}).setdefault(
        "last_run_at", {}
    )

    def _sort_key(journal: str) -> tuple[int, str]:
        stamp = last_run.get(journal, "")
        try:
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (int(parsed.timestamp()), journal)
        except (ValueError, TypeError):
            return (0, journal)

    return sorted(candidates, key=_sort_key)[:max_journals]


def main() -> int:
    global STATE_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=str(HISTORY_CONFIG),
        help="history config yaml (default top5-history.yml)",
    )
    parser.add_argument(
        "--state",
        default=str(STATE_PATH),
        help="resumable state json path",
    )
    parser.add_argument("--journal", default="ALL", help="journal key or ALL")
    parser.add_argument(
        "--journals",
        default="",
        help="comma-separated journal keys for an explicit parallel shard; "
        "when set, rotation is disabled",
    )
    parser.add_argument("--from-year", type=int, default=2025)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--refresh-discovery-only",
        action="store_true",
        help="migrate legacy state, re-gate existing archives and persist "
        "official discovery snapshots without collecting or translating",
    )
    parser.add_argument("--max-issues", type=int, default=6)
    parser.add_argument("--max-translations", type=int, default=80)
    parser.add_argument("--max-journals", type=int, default=4)
    parser.add_argument(
        "--print-pending-journals",
        action="store_true",
        help="print a JSON list of up to --max-journals journals with "
        "actionable pending work and exit (no collection happens)",
    )
    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="regenerate per-journal archive indexes and global search/health "
        "indexes without collecting anything (used by the batch aggregate job)",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="write the batch report JSON to this path for notifications",
    )
    parser.add_argument(
        "--skip-global-indexes",
        action="store_true",
        help="skip regenerating global collections/health/search indexes "
        "(used by parallel per-journal runs so they don't publish stale "
        "aggregate files from their partial snapshot)",
    )
    parser.add_argument(
        "--max-minutes",
        type=int,
        default=35,
        help="stop cleanly after this many minutes per journal (0=unlimited); "
        "partial progress is still published so a cancelled runner loses "
        "at most the in-flight issue",
    )
    args = parser.parse_args()
    STATE_PATH = Path(args.state)
    history_path = Path(args.config)
    history = yaml.safe_load(history_path.read_text(encoding="utf-8"))
    journals = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))["journals"]
    requested_journals = [
        value.strip() for value in str(args.journals).split(",") if value.strip()
    ]
    if requested_journals and args.journal != "ALL":
        parser.error("use either --journal or --journals, not both")
    unknown = [key for key in requested_journals if key not in history["journals"]]
    if unknown:
        parser.error(f"unknown journal key(s): {', '.join(unknown)}")
    if args.journal != "ALL" and args.journal not in history["journals"]:
        parser.error(f"unknown journal key: {args.journal}")
    years = range(args.from_year, args.to_year + 1)
    selected = (
        requested_journals
        if requested_journals
        else [
            key
            for key in history["journals"]
            if args.journal == "ALL" or key == args.journal
        ]
    )

    state_path = Path(args.state)
    state = load_json(state_path, {"schema_version": "1.0", "issues": {}})
    if args.plan_only:
        plan = plan_from_discovery(state, selected, years)
        print(
            json.dumps(
                [issue.__dict__ for issue in plan],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if migrate_legacy_state(state, journals):
        atomic_write_json(state_path, state)
    scoped_history = {
        "journals": {key: history["journals"][key] for key in selected}
    }

    if args.refresh_discovery_only:
        refreshed: dict[str, int] = {}
        for key in selected:
            discovered = discover_official_issues(
                key, history["journals"][key], years=years
            )
            record_discovery(state, key, discovered, history["journals"][key])
            refreshed[key] = len(discovered)
        print(
            json.dumps(
                {
                    "refresh_discovery_only": True,
                    "journals": refreshed,
                    "issue_count": sum(refreshed.values()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    # The pending check must not hit publisher APIs: the workflow runs it every
    # scheduled round and it should stay cheap when nothing is due.
    if args.print_pending_journals:
        chosen = rotate_journals(
            state,
            scoped_history,
            years=years,
            max_journals=args.max_journals,
            journals=journals,
        )
        print(json.dumps(chosen))
        return 0

    if args.aggregate_only:
        for key in selected:
            write_archive_index(
                journals[key]["id"],
                journals[key]["name"],
                updated_at=now_iso(),
            )
        if not args.skip_global_indexes:
            update_indexes(journals, load_available_issues(journals, {}))
        print(json.dumps({"aggregate_only": True, "journals": selected}))
        return 0

    # ALL chooses from the persisted discovery snapshots before doing network
    # work.  Only the selected journals are refreshed, avoiding 49 archive
    # requests merely to decide which four journals should run.
    collection_selected = selected
    if args.journal == "ALL" and not requested_journals:
        collection_selected = rotate_journals(
            state,
            scoped_history,
            years=years,
            max_journals=args.max_journals,
            journals=journals,
        )
        if not collection_selected:
            print(json.dumps({"results": [], "remaining_translation_budget": 0}))
            return 0

    plan: list[HistoricalIssue] = []
    for key in collection_selected:
        discovered = discover_official_issues(
            key, history["journals"][key], years=years
        )
        plan.extend(discovered)
        record_discovery(state, key, discovered, history["journals"][key])
    plan.sort(key=historical_issue_sort_key)

    def _needs_processing(issue: HistoricalIssue) -> bool:
        entry = state.get("issues", {}).get(issue.issue_id, {})
        archive = (
            PUBLIC_API
            / "journals"
            / journals[issue.journal]["id"]
            / "issues"
            / f"{issue.issue_id}.json"
        )
        archive_integrity = inspect_archive(
            archive,
            expected_issue_id=issue.issue_id,
            expected_journal_id=str(journals[issue.journal]["id"]),
        )
        if archive_integrity.get("publication_state") == "ready":
            return str(entry.get("status", "")) != "ready"
        if not entry:
            return True
        if str(entry.get("status", "")) in READY_STATUSES | LEGACY_READY_STATUSES:
            return True
        return is_actionable(entry)

    pending_by_journal: dict[str, list[HistoricalIssue]] = {}
    for issue in plan:
        if _needs_processing(issue):
            pending_by_journal.setdefault(issue.journal, []).append(issue)

    if not pending_by_journal:
        print(json.dumps({"results": [], "remaining_translation_budget": 0}))
        return 0

    reports: list[dict[str, Any]] = []
    import time as _time
    time_budget_reached = False
    summary_lines = [
        "| Journal | Issue | Result | Detail |",
        "|---|---|---|---|",
    ]
    for journal_key in pending_by_journal:
        remaining_translations = args.max_translations
        started_at = _time.monotonic()
        budget_seconds = args.max_minutes * 60 if args.max_minutes > 0 else 0
        for issue in pending_by_journal[journal_key][: args.max_issues]:
            if args.translate and remaining_translations <= 0:
                break
            if budget_seconds and (_time.monotonic() - started_at) >= budget_seconds:
                time_budget_reached = True
                print(
                    f"[backfill] {journal_key} time budget {args.max_minutes}m "
                    "reached; publishing partial progress"
                )
                break
            report = run_issue(
                issue,
                journals[issue.journal],
                state,
                translate=args.translate,
                max_translations=remaining_translations,
            )
            reports.append(report)
            translation = report.get("translation") or {}
            remaining_translations -= int(translation.get("translated", 0))
            result = report.get("result", "")
            detail = report.get("error") or report.get("issue_id", "")
            print(f"[backfill] {issue.issue_id}: {result} {detail}")
            summary_lines.append(
                f"| {issue.journal} | {issue.issue_id} | {result} | "
                f"{str(detail).replace('|', '/')} |"
            )
        state.setdefault("rotation", {}).setdefault("last_run_at", {})[
            journal_key
        ] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        atomic_write_json(state_path, state)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("### Field history backfill batch\n")
            handle.write("\n".join(summary_lines) + "\n")
    for key in pending_by_journal:
        write_archive_index(
            journals[key]["id"],
            journals[key]["name"],
            updated_at=now_iso(),
        )
    if not args.skip_global_indexes:
        update_indexes(journals, load_available_issues(journals, {}))
    final_report = {
        "results": reports,
        "remaining_translation_budget": 0,
        "time_budget_reached": time_budget_reached,
    }
    if args.report_json:
        report_path = Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(final_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(final_report, ensure_ascii=False, indent=2))
    # Blocked issues (thin volumes, one-off translation failures) must not
    # discard the rest of the batch's progress. Exit 0 whenever anything was
    # processed; the notification email and status dashboard surface the
    # blocked issues. Exit 1 only when nothing could be processed at all.
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
