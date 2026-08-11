"""Render truthful historical coverage from discovery, state and archives.

Schema 1.2 keeps the legacy ``summary``, ``journals``, ``periods`` and
``years`` fields.  Coverage fields are derived from persisted discovery
snapshots and archive read-back, never from the number of registered state
entries.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_history import inspect_archive


DEFAULT_API_ROOT = ROOT / "public" / "api" / "v1"
DEFAULT_JOURNALS_CONFIG = ROOT / "config" / "journals.yml"
COVERAGE_COUNTERS = (
    "discovered",
    "archived",
    "content_ready",
    "source_verified",
    "publication_ready",
    "missing",
    "source_pending",
)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1.1", "issues": {}, "discovery": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: state must be a JSON object")
    return payload


def load_journals(path: Path = DEFAULT_JOURNALS_CONFIG) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    journals = payload.get("journals", {}) if isinstance(payload, dict) else {}
    if not isinstance(journals, dict):
        raise ValueError(f"{path}: journals must be a mapping")
    return journals


def summarize(issues: dict[str, Any]) -> dict[str, int]:
    """Return legacy counters while treating only true ready as complete."""

    counts = {
        "complete": 0,
        "translation_partial": 0,
        "blocked": 0,
        "collected": 0,
        "pending": 0,
    }
    for entry in issues.values():
        status = str(entry.get("status", "") or "pending")
        if status == "ready":
            status = "complete"
        elif status == "source_pending":
            status = "pending"
        counts[status if status in counts else "pending"] += 1
    return counts


def period_label(path: Path) -> str:
    match = re.search(r"(\d{4})-(\d{4})", path.name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return path.stem


def _archive_path(
    issue_id: str,
    journal_key: str,
    journals: dict[str, Any],
    api_root: Path,
) -> Path | None:
    config = journals.get(journal_key) or journals.get(journal_key.upper())
    if not config:
        return None
    return api_root / "journals" / str(config["id"]) / "issues" / f"{issue_id}.json"


def reconcile_entries(
    issues: dict[str, Any],
    journals: dict[str, Any],
    api_root: Path,
) -> dict[str, dict[str, Any]]:
    """Overlay archive truth on state records without mutating checkpoints."""

    reconciled: dict[str, dict[str, Any]] = {}
    for issue_id, raw_entry in issues.items():
        entry = dict(raw_entry) if isinstance(raw_entry, dict) else {}
        journal_key = str(entry.get("journal", ""))
        archive = _archive_path(issue_id, journal_key, journals, api_root)
        if archive is not None:
            config = journals.get(journal_key) or journals.get(journal_key.upper())
            assert config is not None
            integrity = inspect_archive(
                archive,
                expected_issue_id=issue_id,
                expected_journal_id=str(config["id"]),
            )
            if integrity.get("archive_exists"):
                entry.update(
                    status=str(integrity["publication_state"]),
                    content_status=str(integrity["content_status"]),
                    source_status=str(integrity["source_status"]),
                    publication_state=str(integrity["publication_state"]),
                )
                if integrity.get("reason"):
                    entry["last_error"] = str(integrity["reason"])
            elif str(entry.get("status", "")) in {"complete", "ready"}:
                entry.update(
                    status="blocked",
                    content_status="blocked",
                    source_status="source_pending",
                    publication_state="blocked",
                    last_error="archive_missing",
                )
        reconciled[issue_id] = entry
    return reconciled


def group_by_journal(issues: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_journal: dict[str, list[dict[str, Any]]] = {}
    for issue_id, entry in sorted(issues.items()):
        journal = str(entry.get("journal", "?"))
        by_journal.setdefault(journal, []).append(
            {
                "issue_id": issue_id,
                "year": entry.get("year"),
                "volume": entry.get("volume"),
                "issue": entry.get("issue"),
                "status": str(entry.get("status", "") or "pending"),
                "content_status": str(entry.get("content_status", "blocked")),
                "source_status": str(entry.get("source_status", "source_pending")),
                "publication_state": str(entry.get("publication_state", "blocked")),
                "last_error": str(entry.get("last_error", "")),
                "retry_class": entry.get("retry_class", ""),
            }
        )
    return by_journal


def discovery_expectations(
    states: Iterable[dict[str, Any]],
    merged_issues: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Union per-period discovery snapshots into issue-level expectations."""

    expected: dict[str, dict[str, Any]] = {}
    for state in states:
        discovery = state.get("discovery", {})
        if not isinstance(discovery, dict):
            continue
        for journal, snapshot in discovery.items():
            if not isinstance(snapshot, dict):
                continue
            issue_years = snapshot.get("issue_years", {}) or {}
            for issue_id in snapshot.get("issue_ids", []):
                issue_id = str(issue_id)
                entry = merged_issues.get(issue_id, {})
                year = issue_years.get(issue_id) or entry.get("year")
                expected[issue_id] = {
                    "issue_id": issue_id,
                    "journal": str(journal),
                    "year": year,
                    "authority": str(snapshot.get("authority", "")),
                    "refreshed_at": str(snapshot.get("refreshed_at", "")),
                    "collector_revision": str(snapshot.get("collector_revision", "")),
                }
    return expected


def _empty_coverage() -> dict[str, Any]:
    return {
        **{key: 0 for key in COVERAGE_COUNTERS},
        "missing_issue_ids": [],
        "source_pending_issue_ids": [],
        "blocked_issue_ids": [],
    }


def _add_coverage(target: dict[str, Any], record: dict[str, Any]) -> None:
    for key in COVERAGE_COUNTERS:
        target[key] += int(record.get(key, 0))
    for key in ("missing_issue_ids", "source_pending_issue_ids", "blocked_issue_ids"):
        issue_id = record.get(key.removesuffix("_ids"))
        if issue_id and issue_id not in target[key]:
            target[key].append(issue_id)


def build_coverage(
    expected: dict[str, dict[str, Any]],
    issues: dict[str, Any],
    journals: dict[str, Any],
    api_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build global, per-journal and per-year archive coverage."""

    overall = _empty_coverage()
    by_journal = {key: {**_empty_coverage(), "years": {}} for key in journals}
    by_year: dict[str, dict[str, Any]] = {}
    for issue_id, expectation in sorted(expected.items()):
        journal = str(expectation["journal"])
        year = str(expectation.get("year") or "unknown")
        config = journals.get(journal) or journals.get(journal.upper())
        if config is None:
            integrity = {
                "archive_exists": False,
                "content_status": "blocked",
                "source_status": "source_pending",
                "publication_state": "blocked",
            }
        else:
            archive = _archive_path(issue_id, journal, journals, api_root)
            assert archive is not None
            integrity = inspect_archive(
                archive,
                expected_issue_id=issue_id,
                expected_journal_id=str(config["id"]),
            )
        archive_exists = bool(integrity.get("archive_exists"))
        content_ready = integrity.get("content_status") == "complete"
        source_verified = integrity.get("source_status") in {
            "official_verified",
            "publisher_verified",
        }
        publication_ready = integrity.get("publication_state") == "ready"
        source_pending = (
            archive_exists and integrity.get("source_status") == "source_pending"
        )
        blocked = archive_exists and integrity.get("publication_state") == "blocked"
        record = {
            "discovered": 1,
            "archived": int(archive_exists),
            "content_ready": int(content_ready),
            "source_verified": int(source_verified),
            "publication_ready": int(publication_ready),
            "missing": int(not archive_exists),
            "source_pending": int(source_pending),
            "missing_issue": issue_id if not archive_exists else "",
            "source_pending_issue": issue_id if source_pending else "",
            "blocked_issue": issue_id if blocked else "",
        }
        _add_coverage(overall, record)
        journal_bucket = by_journal.setdefault(
            journal, {**_empty_coverage(), "years": {}}
        )
        _add_coverage(journal_bucket, record)
        journal_year = journal_bucket["years"].setdefault(year, _empty_coverage())
        _add_coverage(journal_year, record)
        year_bucket = by_year.setdefault(
            year, {**_empty_coverage(), "by_journal": {}}
        )
        _add_coverage(year_bucket, record)
        year_journal = year_bucket["by_journal"].setdefault(
            journal, _empty_coverage()
        )
        _add_coverage(year_journal, record)
    for bucket in [overall, *by_journal.values(), *by_year.values()]:
        for key in ("missing_issue_ids", "source_pending_issue_ids", "blocked_issue_ids"):
            bucket[key].sort()
    for journal_bucket in by_journal.values():
        for bucket in journal_bucket["years"].values():
            for key in ("missing_issue_ids", "source_pending_issue_ids", "blocked_issue_ids"):
                bucket[key].sort()
    for year_bucket in by_year.values():
        for bucket in year_bucket["by_journal"].values():
            for key in ("missing_issue_ids", "source_pending_issue_ids", "blocked_issue_ids"):
                bucket[key].sort()
    return overall, by_journal, by_year


def render_markdown(payload: dict[str, Any]) -> str:
    coverage = payload["coverage"]
    lines = [
        "# Field journal history backfill status",
        "",
        f"Updated: {payload['updated_at']}",
        "",
        (
            "Discovery coverage: "
            f"{coverage['discovered']} discovered · {coverage['archived']} archived · "
            f"{coverage['publication_ready']} publication ready · "
            f"{coverage['missing']} missing · {coverage['source_pending']} source pending"
        ),
        "",
        "| Period | Complete | Partial | Blocked | Pending |",
        "|---|---|---|---|---|",
    ]
    for label in sorted(payload.get("periods", {})):
        summary = payload["periods"][label]["summary"]
        lines.append(
            f"| {label} | {summary['complete']} | "
            f"{summary['translation_partial']} | {summary['blocked']} | "
            f"{summary['pending']} |"
        )
    lines.extend(
        [
            "",
            "| Journal | Issue | Year | Status | Note |",
            "|---|---|---|---|---|",
        ]
    )
    for journal in sorted(payload["journals"]):
        for item in payload["journals"][journal]:
            note = str(item.get("last_error", "")).replace("|", "/")[:80]
            lines.append(
                f"| {journal} | {item['issue_id']} | {item['year']} | "
                f"{item['publication_state']} | {note} |"
            )
    return "\n".join(lines) + "\n"


def build_payload(
    state_paths: list[Path],
    *,
    journals: dict[str, Any],
    api_root: Path,
) -> dict[str, Any]:
    states: list[dict[str, Any]] = []
    merged_issues: dict[str, Any] = {}
    raw_periods: list[tuple[str, dict[str, Any]]] = []
    for state_path in state_paths:
        state = load_state(state_path)
        states.append(state)
        issues = state.get("issues", {}) if isinstance(state.get("issues"), dict) else {}
        raw_periods.append((period_label(state_path), state))
        merged_issues.update(issues)
    reconciled = reconcile_entries(merged_issues, journals, api_root)
    expected = discovery_expectations(states, reconciled)
    coverage, journal_coverage, year_coverage = build_coverage(
        expected, reconciled, journals, api_root
    )

    periods: dict[str, dict[str, Any]] = {}
    for label, state in raw_periods:
        issue_ids = list((state.get("issues", {}) or {}).keys())
        period_issues = {
            issue_id: reconciled[issue_id]
            for issue_id in issue_ids
            if issue_id in reconciled
        }
        period_expected = discovery_expectations([state], reconciled)
        period_coverage, _, _ = build_coverage(
            period_expected, reconciled, journals, api_root
        )
        periods[label] = {
            "summary": summarize(period_issues),
            "issue_count": len(period_issues),
            "updated_at": str(state.get("updated_at", "")),
            "coverage": period_coverage,
        }

    year_entries: dict[str, dict[str, Any]] = {}
    for issue_id, entry in reconciled.items():
        year = str(entry.get("year") or "")
        if year:
            year_entries.setdefault(year, {})[issue_id] = entry
    years: dict[str, dict[str, Any]] = {}
    for year in sorted(set(year_entries) | set(year_coverage)):
        bucket = year_entries.get(year, {})
        coverage_bucket = year_coverage.get(
            year, {**_empty_coverage(), "by_journal": {}}
        )
        years[year] = {
            "summary": summarize(bucket),
            "issue_count": len(bucket),
            "journals": sorted(
                {str(entry.get("journal", "?")) for entry in bucket.values()}
            ),
            "coverage": {
                key: value
                for key, value in coverage_bucket.items()
                if key != "by_journal"
            },
            "by_journal": coverage_bucket.get("by_journal", {}),
        }

    return {
        "schema_version": "1.2",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": summarize(reconciled),
        "coverage": coverage,
        "journal_coverage": journal_coverage,
        "journals": group_by_journal(reconciled),
        "periods": periods,
        "years": years,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state",
        action="append",
        required=True,
        help="resumable state json path (repeatable; one per period)",
    )
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default="")
    parser.add_argument("--api-root", default=str(DEFAULT_API_ROOT))
    parser.add_argument("--journals-config", default=str(DEFAULT_JOURNALS_CONFIG))
    args = parser.parse_args()

    payload = build_payload(
        [Path(value) for value in args.state],
        journals=load_journals(Path(args.journals_config)),
        api_root=Path(args.api_root),
    )
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], sort_keys=True))
    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_markdown(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
