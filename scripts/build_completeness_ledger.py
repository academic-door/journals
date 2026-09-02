"""Build the 2026 expected-vs-observed completeness ledger for tracked journals.

Reusable, deterministic production capability.  For every tracked journal it
measures which 2026 issues the authoritative sources have published and which of
those Academic Door has saved.  It is derived exclusively from persisted
discovery snapshots and archive read-back, never from config ``year_ranges``
(which cannot prove that an expected issue exists).

The script is intentionally self-contained (stdlib + ``yaml`` only) so it can be
run in the data/runtime environment without the collector stack.

Status semantics (factual, not aspirational):

``COMPLETE``        authoritative expected set established and missing == 0
``PARTIAL``         authoritative expected set established and specific missing
                    issues are known
``NOT_MEASURED``    no authoritative expected set can be established yet
``SOURCE_BLOCKED``  the authoritative source was attempted but is externally
                    blocked (no usable expected/authority evidence)
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


WINDOW_START = "2026-01-01"
VALID_STATUSES = {"COMPLETE", "PARTIAL", "NOT_MEASURED", "SOURCE_BLOCKED"}
_BLOCKED_PUBLICATION_STATES = {"blocked"}
_BLOCKED_SOURCE_STATUSES = {"source_pending", "blocked"}
_BLOCKED_AUTHORITY_MARKERS = (
    "captcha",
    "blocked",
    "403",
    "forbidden",
    "paywall",
    "robots",
    "cloudflare",
)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _year(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ""


def load_journals(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    journals = payload.get("journals", {}) if isinstance(payload, dict) else {}
    if not isinstance(journals, dict):
        raise ValueError(f"{path}: journals must be a mapping")
    return journals


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1.1", "issues": {}, "discovery": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: state must be a JSON object")
    return payload


def _journal_id(journal_key: str, config: dict[str, Any]) -> str:
    return str(config.get("id") or journal_key.casefold())


def _archive_path(api_root: Path, config: dict[str, Any], issue_id: str) -> Path:
    return (
        api_root
        / "journals"
        / str(config.get("id", ""))
        / "issues"
        / f"{issue_id}.json"
    )


def _inspect_archive(
    archive: Path, expected_issue_id: str, expected_journal_id: str
) -> dict[str, Any]:
    """Lightweight archive read-back (no collector stack required)."""
    if not archive.exists():
        return {
            "archive_exists": False,
            "content_status": "blocked",
            "source_status": "source_pending",
            "publication_state": "blocked",
            "reason": "",
        }
    try:
        payload = json.loads(archive.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "archive_exists": False,
            "content_status": "blocked",
            "source_status": "source_pending",
            "publication_state": "blocked",
            "reason": "archive_unreadable",
        }
    status = str(payload.get("status", "") or payload.get("publication_state", ""))
    source_status = str(payload.get("source_status", ""))
    content_status = str(payload.get("content_status", ""))
    reason = str(payload.get("reason", ""))
    archive_exists = status in {"ready", "complete", "blocked", "source_pending"} or bool(
        payload.get("archive_exists", True)
    )
    publication_state = "ready" if status in {"ready", "complete"} else status
    return {
        "archive_exists": archive_exists,
        "content_status": content_status or ("complete" if status in {"ready", "complete"} else "blocked"),
        "source_status": source_status,
        "publication_state": publication_state,
        "reason": reason,
    }


def _snapshot_present(states: Iterable[dict[str, Any]], journal: str) -> bool:
    """A discovery snapshot establishes the authoritative expected set.

    Only a real discovery snapshot is a valid source of expected issues; config
    year_ranges are deliberately ignored.
    """
    for state in states:
        discovery = state.get("discovery", {})
        if not isinstance(discovery, dict):
            continue
        snapshot = discovery.get(journal) or discovery.get(journal.upper())
        if isinstance(snapshot, dict):
            return True
    return False


def _source_blocked(authority: str, entry: dict[str, Any], integrity: dict[str, Any]) -> bool:
    """Only a genuine upstream/publisher block counts as SOURCE_BLOCKED.

    A simply-missing archive must never be treated as a source block; its default
    placeholder ``publication_state``/``source_status`` are not evidence.  We
    require either an existing-but-blocked archive or explicit text markers in
    the authority / error / reason.
    """
    if integrity.get("archive_exists"):
        if integrity.get("publication_state") in _BLOCKED_PUBLICATION_STATES:
            return True
        if integrity.get("source_status") in _BLOCKED_SOURCE_STATUSES:
            return True
    text = " ".join(
        str(value).casefold()
        for value in (authority, entry.get("last_error", ""), integrity.get("reason", ""))
    )
    return any(marker in text for marker in _BLOCKED_AUTHORITY_MARKERS)


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
            issue_refs = snapshot.get("issue_refs", {}) or {}
            for issue_id in snapshot.get("issue_ids", []):
                issue_id = str(issue_id)
                entry = merged_issues.get(issue_id, {})
                reference = issue_refs.get(issue_id, {})
                if not isinstance(reference, dict):
                    reference = {}
                year = issue_years.get(issue_id) or entry.get("year")
                expected[issue_id] = {
                    "issue_id": issue_id,
                    "journal": str(journal),
                    "year": year,
                    "volume": reference.get("volume") or entry.get("volume", ""),
                    "issue": reference.get("issue") or entry.get("issue", ""),
                    "official_url": reference.get("official_url") or entry.get("official_url", ""),
                    "authority": str(snapshot.get("authority", "")),
                    "refreshed_at": str(snapshot.get("refreshed_at", "")),
                    "collector_revision": str(snapshot.get("collector_revision", "")),
                }
    return expected


def build_ledger(
    *,
    state_paths: Iterable[Path],
    journals: dict[str, Any],
    api_root: Path,
    window_start: str = WINDOW_START,
) -> dict[str, Any]:
    window_start = window_start or WINDOW_START
    window_end = _today()
    window_year = _year(window_start[:4])

    states: list[dict[str, Any]] = []
    merged_issues: dict[str, Any] = {}
    for state_path in state_paths:
        state = load_state(state_path)
        states.append(state)
        issues = state.get("issues", {}) if isinstance(state.get("issues"), dict) else {}
        merged_issues.update(issues)

    expected = discovery_expectations(states, merged_issues)

    expected_ids: dict[str, list[str]] = {}
    observed_ids: dict[str, list[str]] = {}
    missing_ids: dict[str, list[str]] = {}
    blocked_ids: dict[str, list[str]] = {}
    authorities: dict[str, str] = {}

    for issue_id, exp in expected.items():
        if _year(exp.get("year")) != window_year:
            continue
        journal = str(exp.get("journal", ""))
        key = journal if journal in journals else journal.upper()
        if key not in journals:
            continue
        expected_ids.setdefault(key, []).append(issue_id)
        authorities[key] = str(exp.get("authority", ""))
        config = journals.get(key) or {}
        archive = _archive_path(api_root, config, issue_id)
        integrity = _inspect_archive(
            archive, expected_issue_id=issue_id, expected_journal_id=_journal_id(key, config)
        )
        entry = merged_issues.get(issue_id) or {}
        if integrity.get("archive_exists"):
            observed_ids.setdefault(key, []).append(issue_id)
        elif _source_blocked(authorities[key], entry, integrity):
            blocked_ids.setdefault(key, []).append(issue_id)
        else:
            missing_ids.setdefault(key, []).append(issue_id)

    records: list[dict[str, Any]] = []
    for key, config in journals.items():
        journal_id = _journal_id(key, config)
        title = str(config.get("name") or key)
        snapshot = _snapshot_present(states, key)
        exp_ids = sorted(set(expected_ids.get(key, [])))
        obs_ids = sorted(set(observed_ids.get(key, [])))
        mis_ids = sorted(set(missing_ids.get(key, [])))
        blk_ids = sorted(set(blocked_ids.get(key, [])))

        if not snapshot and not exp_ids:
            status = "NOT_MEASURED"
            missing_issues: list[dict[str, Any]] = []
            evidence = "no 2026 discovery snapshot on file"
        elif not mis_ids and not blk_ids:
            status = "COMPLETE"
            missing_issues = []
            evidence = "all expected 2026 issues observed"
        elif not mis_ids and blk_ids:
            status = "SOURCE_BLOCKED"
            missing_issues = []
            evidence = "expected set blocked by upstream/publisher access"
        else:
            status = "PARTIAL"
            missing_issues = [
                {
                    "issue_id": issue_id,
                    "volume": str(expected[issue_id].get("volume", "")) if issue_id in expected else "",
                    "issue": str(expected[issue_id].get("issue", "")) if issue_id in expected else "",
                    "official_url": str(expected[issue_id].get("official_url", "")) if issue_id in expected else "",
                }
                for issue_id in mis_ids
            ]
            evidence = f"{len(mis_ids)} expected 2026 issue(s) not observed"

        records.append(
            {
                "journalId": journal_id,
                "journalKey": key,
                "title": title,
                "windowStart": window_start,
                "windowEnd": window_end,
                "expectedIssues": exp_ids,
                "observedIssues": obs_ids,
                "missingIssues": missing_issues,
                "expectedIssueCount": len(exp_ids),
                "observedIssueCount": len(obs_ids),
                "missingIssueCount": len(mis_ids),
                "status": status,
                "evidence": evidence,
                "lastCheckedAt": window_end,
            }
        )

    records.sort(key=lambda r: (r["journalKey"].casefold(), r["journalId"]))
    counts = {status: 0 for status in VALID_STATUSES}
    for record in records:
        counts[record["status"]] += 1

    reconciliation = {
        "journal_count": len(records),
        "complete": counts["COMPLETE"],
        "partial": counts["PARTIAL"],
        "not_measured": counts["NOT_MEASURED"],
        "source_blocked": counts["SOURCE_BLOCKED"],
        "expected_issues_total": sum(r["expectedIssueCount"] for r in records),
        "observed_issues_total": sum(r["observedIssueCount"] for r in records),
        "confirmed_missing_issues_total": sum(r["missingIssueCount"] for r in records),
    }

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "windowStart": window_start,
        "windowEnd": window_end,
        "scope": "journals_tracked",
        "daily_door_semantics": {
            "daily_door_formal_monitor": 97,
            "journals_tracked": len(journals),
        },
        "reconciliation": reconciliation,
        "journals": records,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    rec = payload["reconciliation"]
    lines = [
        "# Academic Door 2026 completeness ledger",
        "",
        f"Window: {payload['windowStart']} -> {payload['windowEnd']}",
        f"Generated: {payload['generated_at']}",
        "",
        "## Summary",
        "",
        f"- journals tracked: {rec['journal_count']}",
        f"- COMPLETE: {rec['complete']}",
        f"- PARTIAL: {rec['partial']}",
        f"- NOT_MEASURED: {rec['not_measured']}",
        f"- SOURCE_BLOCKED: {rec['source_blocked']}",
        f"- expected issues: {rec['expected_issues_total']}",
        f"- observed issues: {rec['observed_issues_total']}",
        f"- confirmed missing issues: {rec['confirmed_missing_issues_total']}",
        "",
        "## Per-journal",
        "",
        "| Journal | Status | Expected | Observed | Missing | Evidence |",
        "|---|---|---:|---:|---:|---|",
    ]
    for record in payload["journals"]:
        evidence = re.sub(r"\s+", " ", str(record.get("evidence", ""))).replace("|", "/")
        lines.append(
            f"| {record['journalKey']} | {record['status']} | "
            f"{record['expectedIssueCount']} | {record['observedIssueCount']} | "
            f"{record['missingIssueCount']} | {evidence[:80]} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", action="append", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default="")
    parser.add_argument("--api-root", default="public/api/v1")
    parser.add_argument("--journals-config", default=str(ROOT / "config" / "journals.yml"))
    parser.add_argument("--window-start", default=WINDOW_START)
    args = parser.parse_args()

    payload = build_ledger(
        state_paths=[Path(value) for value in args.state],
        journals=load_journals(Path(args.journals_config)),
        api_root=Path(args.api_root),
        window_start=args.window_start,
    )
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["reconciliation"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

