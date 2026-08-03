from __future__ import annotations

import argparse
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "journals.yml"
DEFAULT_STATE_PATH = ROOT / "data" / "monitoring" / "state.json"
DEFAULT_RESULT_PATH = ROOT / "output" / "journal-monitor-result.json"
DEFAULT_PUBLIC_STATUS_PATH = ROOT / "public" / "api" / "v1" / "monitoring.json"
CURRENT_ISSUE_ROOT = ROOT / "public" / "api" / "v1" / "journals"
CROSSREF_API = "https://api.crossref.org"
USER_AGENT = (
    "AcademicDoorJournalMonitor/1.0 "
    "(non-profit metadata monitor; https://academic-door.github.io/journals/)"
)
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
STRUCTURAL_TITLE = re.compile(
    r"front\s*matter|back\s*matter|editorial\s*board|table\s*of\s*contents|"
    r"issue\s+information|^correction\b|^erratum\b",
    re.IGNORECASE,
)
ALERT_THRESHOLD = 3
DEEP_RETRY_HOURS = 6
ENTITLEMENT_RETRY_HOURS = 24
TRANSLATION_RETRY_HOURS = 2
AWAITING_OFFICIAL_RETRY_CAP_HOURS = 24
AWAITING_OFFICIAL_ESCALATION_DAYS = 7


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def normalize_doi(value: str) -> str:
    return value.strip().lower().removeprefix("https://doi.org/").rstrip(".,;)")


def issue_fingerprint(issue: dict[str, Any] | None) -> str:
    if not issue:
        return ""
    dois = sorted(
        normalize_doi(str(article.get("doi", "")))
        for article in issue.get("articles", [])
        if article.get("doi")
    )
    material = "|".join(
        [
            str(issue.get("volume", "")),
            str(issue.get("issue", "")),
            *dois,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _date_parts(item: dict[str, Any], key: str) -> tuple[int, int, int] | None:
    values = item.get(key, {}).get("date-parts", [])
    if not values or not values[0]:
        return None
    parts = list(values[0]) + [1, 1]
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except (TypeError, ValueError):
        return None


def item_publication_date(item: dict[str, Any]) -> date | None:
    for key in ("published-print", "published", "issued", "published-online"):
        parts = _date_parts(item, key)
        if parts:
            try:
                return date(*parts)
            except ValueError:
                continue
    return None


def _numeric(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else None


def _baseline_date(issue: dict[str, Any]) -> date | None:
    text = str(issue.get("publication_date", ""))
    for fmt in ("%B %Y", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date().replace(day=1)
        except ValueError:
            continue
    return None


def _publication_cutoff(today: date, lead_months: int) -> date:
    if lead_months <= 0:
        return today
    offset = today.month - 1 + lead_months
    year = today.year + offset // 12
    month = offset % 12 + 1
    return date(year, month, monthrange(year, month)[1])


def _lead_months(config: dict[str, Any]) -> int:
    if "publication_lead_months" in config:
        return max(0, int(config["publication_lead_months"]))
    return 1 if config.get("collector") == "elsevier" else 0


def _is_candidate_group(
    volume: str,
    issue_label: str,
    publication: date | None,
    baseline: dict[str, Any],
) -> bool:
    baseline_volume = str(baseline.get("volume", ""))
    baseline_issue = str(baseline.get("issue", ""))
    if volume == baseline_volume and issue_label == baseline_issue:
        return True
    volume_number = _numeric(volume)
    baseline_volume_number = _numeric(baseline_volume)
    if (
        volume_number is not None
        and baseline_volume_number is not None
        and volume_number > baseline_volume_number
    ):
        return True
    if volume == baseline_volume:
        issue_number = _numeric(issue_label)
        baseline_issue_number = _numeric(baseline_issue)
        if (
            issue_number is not None
            and baseline_issue_number is not None
        ):
            return issue_number > baseline_issue_number
    baseline_publication = _baseline_date(baseline)
    return bool(
        publication
        and baseline_publication
        and publication > baseline_publication
        and (volume != baseline_volume or issue_label != baseline_issue)
    )


@dataclass(frozen=True)
class Candidate:
    issue_key: str
    volume: str
    issue: str
    publication_date: str
    dois: tuple[str, ...]
    unseen_dois: tuple[str, ...]
    fingerprint: str


def select_candidate(
    items: list[dict[str, Any]],
    baseline: dict[str, Any],
    *,
    today: date | None = None,
    publication_lead_months: int = 0,
) -> Candidate | None:
    # Crossref commonly deposits complete future issues weeks in advance. They
    # are useful metadata, but they are not evidence that the official current
    # issue has changed, so detection never looks beyond today.
    current = today or datetime.now(timezone.utc).date()
    cutoff = _publication_cutoff(current, publication_lead_months)
    baseline_dois = {
        normalize_doi(str(article.get("doi", "")))
        for article in baseline.get("articles", [])
        if article.get("doi")
    }
    excluded_dois = {
        normalize_doi(str(item.get("doi", "")))
        for item in baseline.get("quality", {}).get("excluded_items", [])
        if item.get("doi")
    }
    excluded_titles = {
        " ".join(str(item.get("title_en", "")).casefold().split())
        for item in baseline.get("quality", {}).get("excluded_items", [])
        if item.get("title_en")
    }
    grouped: dict[tuple[str, str], list[tuple[dict[str, Any], date | None]]] = {}
    for item in items:
        if str(item.get("type", "journal-article")) != "journal-article":
            continue
        title = " ".join(str(value) for value in item.get("title", []))
        doi = normalize_doi(str(item.get("DOI", "")))
        if not doi:
            continue
        normalized_title = " ".join(title.casefold().split())
        if (
            STRUCTURAL_TITLE.search(title)
            or doi in excluded_dois
            or normalized_title in excluded_titles
        ):
            continue
        publication = item_publication_date(item)
        if publication and publication > cutoff:
            continue
        key = (str(item.get("volume", "")).strip(), str(item.get("issue", "")).strip())
        # Online-first deposits without an assigned volume/issue are article
        # alerts, not proof that a new journal issue is publicly current.
        if not key[0] and not key[1]:
            continue
        grouped.setdefault(key, []).append((item, publication))

    candidates: list[tuple[tuple[date, int, int], Candidate]] = []
    for (volume, issue_label), records in grouped.items():
        publications = [published for _item, published in records if published]
        publication = max(publications) if publications else None
        if not _is_candidate_group(volume, issue_label, publication, baseline):
            continue
        dois = sorted(
            {
                normalize_doi(str(item.get("DOI", "")))
                for item, _published in records
                if item.get("DOI")
            }
        )
        unseen = sorted(set(dois) - baseline_dois)
        if not unseen:
            continue
        issue_key = f"{volume}:{issue_label}"
        fingerprint = hashlib.sha256(
            "|".join([issue_key, *dois]).encode("utf-8")
        ).hexdigest()
        candidate = Candidate(
            issue_key=issue_key,
            volume=volume,
            issue=issue_label,
            publication_date=publication.isoformat() if publication else "",
            dois=tuple(dois),
            unseen_dois=tuple(unseen),
            fingerprint=fingerprint,
        )
        score = (
            publication or date.min,
            _numeric(volume) or -1,
            _numeric(issue_label) or -1,
        )
        candidates.append((score, candidate))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _request_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
    attempts: int = 3,
    timeout: int = 45,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt + 1 < attempts:
                retry_after = getattr(error.response, "headers", {}).get(
                    "Retry-After", ""
                )
                try:
                    delay = min(float(retry_after), 30.0)
                except (TypeError, ValueError):
                    delay = (2**attempt) + random.uniform(0.1, 0.8)
                time.sleep(delay)
    raise RuntimeError(f"metadata request failed after {attempts} attempts: {last_error}")


def fetch_crossref_items(
    config: dict[str, Any],
    baseline: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    client = session or requests.Session()
    client.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    baseline_date = _baseline_date(baseline)
    start_year = (baseline_date.year - 1) if baseline_date else datetime.now().year - 1
    until = _publication_cutoff(
        datetime.now(timezone.utc).date(),
        _lead_months(config),
    )
    response = _request_with_retry(
        client,
        f"{CROSSREF_API}/journals/{config['issn']}/works",
        params={
            "filter": (
                f"from-pub-date:{start_year}-01-01,"
                f"until-pub-date:{until.isoformat()},type:journal-article"
            ),
            "sort": "published",
            "order": "desc",
            "rows": "100",
        },
    )
    payload = response.json()
    items = payload.get("message", {}).get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("Crossref returned an invalid item list")
    return [item for item in items if isinstance(item, dict)]


def fetch_rss_dois(
    rss_url: str,
    *,
    session: requests.Session | None = None,
) -> set[str]:
    if not rss_url:
        return set()
    client = session or requests.Session()
    client.headers.update({"User-Agent": USER_AGENT, "Accept": "application/rss+xml"})
    response = _request_with_retry(client, rss_url, attempts=2, timeout=30)
    root = ElementTree.fromstring(response.content)
    text = " ".join(value.strip() for value in root.itertext() if value.strip())
    return {normalize_doi(match.group(0)) for match in DOI_PATTERN.finditer(text)}


def _candidate_payload(candidate: Candidate) -> dict[str, Any]:
    return {
        "issue_key": candidate.issue_key,
        "volume": candidate.volume,
        "issue": candidate.issue,
        "publication_date": candidate.publication_date,
        "fingerprint": candidate.fingerprint,
        "doi_count": len(candidate.dois),
        "unseen_dois": list(candidate.unseen_dois),
    }


def evaluate_observation(
    candidate: Candidate | None,
    baseline: dict[str, Any],
    previous_entry: dict[str, Any],
    *,
    rss_dois: set[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    if candidate is None:
        return "unchanged", {
            "candidate": None,
            "candidate_seen_count": 0,
            "evidence": ["crossref"],
        }
    previous_candidate = previous_entry.get("candidate") or {}
    seen_count = (
        int(previous_entry.get("candidate_seen_count", 0)) + 1
        if previous_candidate.get("fingerprint") == candidate.fingerprint
        else 1
    )
    evidence = ["crossref"]
    if rss_dois and set(candidate.unseen_dois) & rss_dois:
        evidence.append("official_rss")

    same_issue = candidate.issue_key == (
        f"{baseline.get('volume', '')}:{baseline.get('issue', '')}"
    )
    baseline_volume = _numeric(str(baseline.get("volume", "")))
    candidate_volume = _numeric(candidate.volume)
    clearly_new_issue = bool(
        candidate_volume is not None
        and baseline_volume is not None
        and candidate_volume > baseline_volume
    )
    confirmed = (
        "official_rss" in evidence
        or same_issue
        or clearly_new_issue
        or seen_count >= 2
    )
    return ("confirmed" if confirmed else "candidate"), {
        "candidate": _candidate_payload(candidate),
        "candidate_seen_count": seen_count,
        "evidence": evidence,
    }


def _current_issue_path(config: dict[str, Any]) -> Path:
    return CURRENT_ISSUE_ROOT / config["id"] / "issues" / "current.json"


def detect_all(
    journal_configs: dict[str, dict[str, Any]],
    state: dict[str, Any],
    *,
    crossref_fetcher: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]] = fetch_crossref_items,
    rss_fetcher: Callable[[str], set[str]] = fetch_rss_dois,
) -> tuple[dict[str, Any], dict[str, Any]]:
    previous_entries = state.get("journals", {})
    next_entries: dict[str, Any] = {}
    confirmed: list[str] = []
    pending: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    newly_alerting: list[str] = []
    recovered: list[str] = []
    checked_at = now_iso()

    prepared: list[
        tuple[str, dict[str, Any], dict[str, Any], dict[str, Any] | None]
    ] = []
    for key, config in journal_configs.items():
        if not config.get("enabled"):
            continue
        previous = previous_entries.get(key, {})
        baseline = read_json(_current_issue_path(config))
        prepared.append((key, config, previous, baseline))

    probe_results: dict[str, tuple[list[dict[str, Any]] | None, Exception | None]] = {}
    fetchable = [item for item in prepared if item[3] is not None]
    if fetchable:
        with ThreadPoolExecutor(max_workers=min(2, len(fetchable))) as executor:
            future_to_key = {
                executor.submit(crossref_fetcher, config, baseline): key
                for key, config, _previous, baseline in fetchable
                if baseline is not None
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    probe_results[key] = (future.result(), None)
                except Exception as error:
                    probe_results[key] = (None, error)
        # A proxy or Crossref edge can drop the first concurrent connections.
        # Give only failed journals one quiet sequential second chance.
        for key, config, _previous, baseline in fetchable:
            _items, probe_error = probe_results.get(key, (None, None))
            if not probe_error or baseline is None:
                continue
            try:
                time.sleep(random.uniform(0.2, 0.7))
                probe_results[key] = (crossref_fetcher(config, baseline), None)
            except Exception as error:
                probe_results[key] = (None, error)

    for key, config, previous, baseline in prepared:
        previous_failures = int(previous.get("failure_count", 0))
        if not baseline:
            failure_count = previous_failures + 1
            entry = {
                **previous,
                "journal_id": config["id"],
                "status": "failed",
                "last_checked_at": checked_at,
                "failure_count": failure_count,
                "last_error": "last-known-good issue is missing",
            }
            failed.append(key)
        else:
            try:
                items, probe_error = probe_results.get(
                    key,
                    (None, RuntimeError("Crossref probe produced no result")),
                )
                if probe_error:
                    raise probe_error
                assert items is not None
                candidate = select_candidate(
                    items,
                    baseline,
                    publication_lead_months=_lead_months(config),
                )
                rss_dois: set[str] = set()
                if candidate and config.get("rss_url"):
                    try:
                        rss_dois = rss_fetcher(str(config["rss_url"]))
                    except Exception:
                        # RSS is corroborating evidence only; Crossref detection continues.
                        rss_dois = set()
                status, observation = evaluate_observation(
                    candidate,
                    baseline,
                    previous,
                    rss_dois=rss_dois,
                )
                previous_candidate = previous.get("candidate") or {}
                observed_candidate = observation.get("candidate") or {}
                same_deep_candidate = bool(
                    observed_candidate
                    and previous_candidate.get("fingerprint")
                    == observed_candidate.get("fingerprint")
                )
                entry = {
                    **previous,
                    **observation,
                    "journal_id": config["id"],
                    "status": status,
                    "last_checked_at": checked_at,
                    "current_fingerprint": issue_fingerprint(baseline),
                    "current_issue_id": baseline.get("issue_id", ""),
                    "failure_count": 0,
                    "deep_failure_count": (
                        int(previous.get("deep_failure_count", 0))
                        if same_deep_candidate
                        else 0
                    ),
                    "last_error": (
                        str(previous.get("last_error", ""))
                        if same_deep_candidate
                        else ""
                    ),
                    "next_deep_retry_at": (
                        str(previous.get("next_deep_retry_at", ""))
                        if same_deep_candidate
                        else ""
                    ),
                }
                if _awaiting_status(previous, same_deep_candidate):
                    # A light probe re-confirming the same Crossref candidate
                    # does not change the fact that we are still waiting for
                    # the official publisher roster.
                    entry["status"] = "awaiting_official"
                if status == "confirmed":
                    confirmed.append(key)
                elif status == "candidate":
                    pending.append(key)
                else:
                    unchanged.append(key)
                if previous_failures >= ALERT_THRESHOLD:
                    recovered.append(key)
                if (
                    int(previous.get("deep_failure_count", 0)) >= ALERT_THRESHOLD
                    and not same_deep_candidate
                    and key not in recovered
                ):
                    recovered.append(key)
            except Exception as error:
                failure_count = previous_failures + 1
                entry = {
                    **previous,
                    "journal_id": config["id"],
                    "status": "failed",
                    "last_checked_at": checked_at,
                    "failure_count": failure_count,
                    "last_error": f"{type(error).__name__}: {error}",
                }
                failed.append(key)
        if (
            int(entry.get("failure_count", 0)) == ALERT_THRESHOLD
            and previous_failures < ALERT_THRESHOLD
        ):
            newly_alerting.append(key)
        next_entries[key] = entry

    next_state = {
        "schema_version": "1.0",
        "updated_at": checked_at,
        "journals": next_entries,
    }
    result = {
        "schema_version": "1.0",
        "checked_at": checked_at,
        "confirmed_journals": confirmed,
        "pending_journals": pending,
        "unchanged_journals": unchanged,
        "failed_journals": failed,
        "alerts": {
            "newly_alerting": newly_alerting,
            "recovered": recovered,
        },
    }
    return next_state, result


def _is_awaiting_official(error_text: str) -> bool:
    """A Crossref-confirmed candidate whose publisher roster is not live yet.

    The architecture refuses to publish provisional Crossref rosters, so the
    deep update correctly waits for the official issue page. This is not an
    operational failure: the journal is simply awaiting publisher
    confirmation.
    """

    return (
        "provisional Crossref roster requires official confirmation"
        in error_text
    )


def _awaiting_backoff_hours(awaiting_count: int) -> int:
    """Exponential backoff for publisher confirmation, capped at 24 hours."""

    return min(2 ** max(0, int(awaiting_count) - 1), AWAITING_OFFICIAL_RETRY_CAP_HOURS)


def _awaiting_status(previous: dict[str, Any], same_deep_candidate: bool) -> bool:
    """Keep an awaiting_official journal awaiting while its candidate is stable."""

    return (
        previous.get("status") == "awaiting_official"
        and bool(same_deep_candidate)
    )


def run_deep_updates(
    confirmed: list[str],
    state: dict[str, Any],
    result: dict[str, Any],
    *,
    translate: bool,
) -> int:
    update_results: list[dict[str, Any]] = []
    failure_total = 0
    for key in confirmed:
        entry = state["journals"][key]
        next_retry_text = str(entry.get("next_deep_retry_at", ""))
        if next_retry_text:
            try:
                next_retry = datetime.fromisoformat(next_retry_text)
            except ValueError:
                next_retry = datetime.min.replace(tzinfo=timezone.utc)
            if next_retry > datetime.now(timezone.utc):
                update_results.append(
                    {
                        "journal": key,
                        "result": "deferred",
                        "error": "",
                        "next_retry_at": next_retry_text,
                    }
                )
                continue
        candidate = entry.get("candidate") or {}
        command = [
            sys.executable,
            str(ROOT / "scripts" / "update_journals.py"),
            "--journal",
            key,
        ]
        if candidate.get("volume"):
            command.extend(["--expected-volume", str(candidate["volume"])])
        if candidate.get("issue"):
            command.extend(["--expected-issue", str(candidate["issue"])])
        if translate:
            command.append("--translate")
        completed = subprocess.run(command, cwd=ROOT, check=False)
        report = read_json(ROOT / "output" / "journal-update-report.json") or {}
        item = (report.get("results") or [{}])[0]
        success = completed.returncode == 0 and item.get("result") == "updated"
        if success:
            if (
                int(entry.get("deep_failure_count", 0)) >= ALERT_THRESHOLD
                and key not in result["alerts"]["recovered"]
            ):
                result["alerts"]["recovered"].append(key)
            entry.update(
                {
                    "status": "updated",
                    "candidate": None,
                    "candidate_seen_count": 0,
                    "failure_count": 0,
                    "deep_failure_count": 0,
                    "awaiting_official_count": 0,
                    "awaiting_official_since": "",
                    "last_error": "",
                    "last_deep_update_at": now_iso(),
                    "last_deep_attempt_at": now_iso(),
                    "next_deep_retry_at": "",
                }
            )
        else:
            previous_failures = int(entry.get("deep_failure_count", 0))
            error_text = str(
                item.get("error")
                or f"deep update exited {completed.returncode}"
            )
            source_lag = "SourceLagError:" in error_text
            entitlement_blocked = "elsevier_insttoken_required" in error_text
            translation_only = "translation incomplete:" in error_text
            awaiting_official = _is_awaiting_official(error_text)
            if awaiting_official:
                awaiting_count = int(entry.get("awaiting_official_count", 0)) + 1
                since_text = str(entry.get("awaiting_official_since") or "")
                try:
                    since_dt = (
                        datetime.fromisoformat(since_text)
                        if since_text
                        else datetime.now(timezone.utc)
                    )
                except ValueError:
                    since_dt = datetime.now(timezone.utc)
                escalated = (datetime.now(timezone.utc) - since_dt) > timedelta(
                    days=AWAITING_OFFICIAL_ESCALATION_DAYS
                )
                entry["awaiting_official_count"] = awaiting_count
                entry["awaiting_official_since"] = since_text or now_iso()
                if not escalated:
                    backoff_hours = _awaiting_backoff_hours(awaiting_count)
                    entry.update(
                        {
                            "status": "awaiting_official",
                            "last_error": error_text,
                            "last_deep_attempt_at": now_iso(),
                            "next_deep_retry_at": (
                                datetime.now(timezone.utc)
                                + timedelta(hours=backoff_hours)
                            ).replace(microsecond=0).isoformat(),
                        }
                    )
                    update_results.append(
                        {
                            "journal": key,
                            "result": "awaiting_official",
                            "error": error_text,
                        }
                    )
                    continue
            retry_hours = (
                ENTITLEMENT_RETRY_HOURS
                if entitlement_blocked
                else TRANSLATION_RETRY_HOURS
                if translation_only
                else DEEP_RETRY_HOURS
            )
            next_retry = datetime.now(timezone.utc) + timedelta(
                hours=retry_hours
            )
            entry.update(
                {
                    "status": (
                        "source_lag"
                        if source_lag
                        else "entitlement_blocked"
                        if entitlement_blocked
                        else "translation_pending"
                        if translation_only
                        else "update_failed"
                    ),
                    "deep_failure_count": previous_failures + 1,
                    "last_error": error_text,
                    "last_deep_attempt_at": now_iso(),
                    "next_deep_retry_at": next_retry.replace(
                        microsecond=0
                    ).isoformat(),
                }
            )
            if (
                entry["deep_failure_count"] == ALERT_THRESHOLD
                and key not in result["alerts"]["newly_alerting"]
            ):
                result["alerts"]["newly_alerting"].append(key)
            failure_total += 1
        update_results.append(
            {
                "journal": key,
                "result": (
                    "updated"
                    if success
                    else (
                        "source_lag"
                        if source_lag
                        else "entitlement_blocked"
                        if entitlement_blocked
                        else "translation_pending"
                        if translation_only
                        else "preserved_previous"
                    )
                ),
                "error": "" if success else entry["last_error"],
            }
        )
    result["deep_updates"] = update_results
    return failure_total


def public_status(
    state: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    entries = state.get("journals", {})
    alerting = [
        key
        for key, entry in entries.items()
        if entry.get("status") != "awaiting_official"
        and (
            int(entry.get("failure_count", 0)) >= ALERT_THRESHOLD
            or int(entry.get("deep_failure_count", 0)) >= ALERT_THRESHOLD
        )
    ]
    warnings = [
        key
        for key, entry in entries.items()
        if entry.get("status") != "awaiting_official"
        and (
            int(entry.get("failure_count", 0)) > 0
            or int(entry.get("deep_failure_count", 0)) > 0
        )
        and key not in alerting
    ]
    awaiting_official = [
        key
        for key, entry in entries.items()
        if entry.get("status") == "awaiting_official"
        and int(entry.get("awaiting_official_count", 0)) > 0
    ]
    return {
        "schema_version": "1.0",
        "updated_at": state.get("updated_at", now_iso()),
        "status": "degraded" if alerting else "healthy",
        "schedule": "every_two_hours",
        "summary": {
            "configured_journals": len(entries),
            "unchanged": len(result.get("unchanged_journals", [])),
            "candidates": len(result.get("pending_journals", [])),
            "confirmed_updates": len(result.get("confirmed_journals", [])),
            "warnings": len(warnings),
            "awaiting_official": len(awaiting_official),
            "failed": len(alerting),
        },
        "warning_journals": warnings,
        "failed_journals": alerting,
        "awaiting_official_journals": awaiting_official,
        "last_successful_checks": {
            key: entry.get("last_checked_at", "")
            for key, entry in entries.items()
            if not entry.get("last_error")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect journal changes cheaply, then deep-update only confirmed journals."
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT_PATH)
    parser.add_argument(
        "--public-status", type=Path, default=DEFAULT_PUBLIC_STATUS_PATH
    )
    parser.add_argument("--run-updates", action="store_true")
    parser.add_argument("--translate", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a failing exit code when a deep update cannot replace the baseline.",
    )
    args = parser.parse_args()

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["journals"]
    state = read_json(args.state) or {
        "schema_version": "1.0",
        "journals": {},
    }
    next_state, result = detect_all(config, state)
    deep_failures = 0
    if args.run_updates:
        deep_failures = run_deep_updates(
            result["confirmed_journals"],
            next_state,
            result,
            translate=args.translate,
        )
    write_json(args.state, next_state)
    write_json(args.result, result)
    write_json(args.public_status, public_status(next_state, result))
    print(json.dumps(result, ensure_ascii=False))
    return 1 if args.strict and deep_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
