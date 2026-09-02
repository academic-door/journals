"""Build the 2026 expected-vs-observed completeness ledger for tracked journals.

Reusable, deterministic production capability.  For every tracked journal it
measures which 2026 issues the authoritative sources have published and which of
those Academic Door has saved.  It is derived exclusively from persisted
discovery snapshots + runtime expected-issue exclusions + archive read-back,
never from config ``year_ranges``.

Authority classification (evidence-based, consistent with the journals history
pipeline):

- ``crossref_candidate``            -> candidate evidence, NOT authoritative
- ``official_archive`` / publisher-verified / official-issue-page  -> authoritative
- any other / unrecognized value    -> unknown -> NOT_MEASURED

Expected-set semantics (per journal, for the audit window):

- ``discoveredCandidateIssues``      every 2026 issue seen in any discovery
                                      snapshot (including crossref candidates)
- ``authoritativeExpectedIssues``    2026 issues from authoritative snapshots only
- ``excludedIssues``                 authoritative issues that the runtime
                                      ``expected_issue_exclusions`` mark as outside
                                      the currently published expected set
                                      (e.g. ``not_yet_published``)
- ``effectiveExpectedIssues``        authoritativeExpectedIssues - excludedIssues

Completeness is computed from ``effectiveExpectedIssues``.  A journal cannot be
COMPLETE merely because ``effectiveExpectedIssues == 0``: COMPLETE requires
positive authoritative evidence that the current published expected set for the
window has been established and every effective expected issue is
publication-ready.
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

NON_AUTHORITATIVE_AUTHORITIES = {"crossref_candidate", ""}
AUTHORITATIVE_AUTHORITIES = {
    "official_archive",
    "official-issue-page",
    "official_issue_page",
    "publisher_verified",
    "publisher_archive",
    "publisher_issue_page",
}
# Runtime exclusion statuses meaning "outside the currently published expected set".
EXCLUSION_OUT_OF_SET_STATUSES = {"not_yet_published", "not_yet_available", "not_published"}


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
        return {"schema_version": "1.1", "issues": {}, "discovery": {}, "expected_issue_exclusions": {}}
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


def _status_fields(status: str) -> dict[str, str]:
    if status == "ready":
        return {"content_status": "complete", "source_status": "official_verified", "publication_state": "ready"}
    if status == "source_pending":
        return {"content_status": "complete", "source_status": "source_pending", "publication_state": "source_pending"}
    if status == "translation_partial":
        return {"content_status": "translation_partial", "source_status": "source_pending", "publication_state": "translation_partial"}
    return {"content_status": "blocked", "source_status": "source_pending", "publication_state": "blocked"}


def inspect_archive(path: Path, *, expected_issue_id: str = "", expected_journal_id: str = "") -> dict[str, Any]:
    """Faithful, self-contained archive inspector (mirrors the canonical one)."""
    if not path.exists():
        return {"archive_exists": False, "archive_valid": False, "content_status": "blocked",
                "source_status": "source_pending", "publication_state": "blocked", "reason": "archive_missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"archive_exists": True, "archive_valid": False, "content_status": "blocked",
                "source_status": "source_pending", "publication_state": "blocked", "reason": "archive_invalid"}
    if not isinstance(payload, dict):
        return {"archive_exists": True, "archive_valid": False, "content_status": "blocked",
                "source_status": "source_pending", "publication_state": "blocked", "reason": "archive_not_object"}
    status = str(payload.get("status", "") or payload.get("publication_state", ""))
    fields = _status_fields(status)
    fields = {
        "content_status": str(payload.get("content_status") or fields["content_status"]),
        "source_status": str(payload.get("source_status") or fields["source_status"]),
        "publication_state": str(payload.get("publication_state") or fields["publication_state"]),
    }
    identity_ok = True
    if expected_issue_id and str(payload.get("issue_id", "")) != expected_issue_id:
        identity_ok = False
    if expected_journal_id and str(payload.get("journal_id", "")) != expected_journal_id:
        identity_ok = False
    if not identity_ok:
        return {"archive_exists": True, "archive_valid": False, "content_status": "blocked",
                "source_status": "source_pending", "publication_state": "blocked", "reason": "archive_identity_mismatch"}
    return {"archive_exists": True, "archive_valid": True, "content_status": fields["content_status"],
            "source_status": fields["source_status"], "publication_state": fields["publication_state"],
            "reason": "", "issue": payload}


def _authority_kind(authority: str) -> str:
    if authority in NON_AUTHORITATIVE_AUTHORITIES:
        return "candidate"
    if authority in AUTHORITATIVE_AUTHORITIES:
        return "authoritative"
    return "unknown"


def _collect_exclusions(states: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Union runtime expected-issue exclusions across all state files."""
    out: dict[str, dict[str, Any]] = {}
    for state in states:
        exclusions = state.get("expected_issue_exclusions", {})
        if isinstance(exclusions, dict):
            for iid, record in exclusions.items():
                out[str(iid)] = record if isinstance(record, dict) else {"status": str(record)}
    return out


def _collect_discovery(states: Iterable[dict[str, Any]], window_year: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Per journal: every discovered 2026 issue with its best authority."""
    candidate: dict[str, dict[str, dict[str, Any]]] = {}
    authoritative: dict[str, dict[str, dict[str, Any]]] = {}
    for state in states:
        discovery = state.get("discovery", {})
        if not isinstance(discovery, dict):
            continue
        for journal, snapshot in discovery.items():
            if not isinstance(snapshot, dict):
                continue
            authority = str(snapshot.get("authority", ""))
            kind = _authority_kind(authority)
            issue_years = snapshot.get("issue_years", {}) or {}
            issue_refs = snapshot.get("issue_refs", {}) or {}
            for issue_id in snapshot.get("issue_ids", []):
                issue_id = str(issue_id)
                if _year(issue_years.get(issue_id)) != window_year:
                    continue
                reference = issue_refs.get(issue_id, {})
                if not isinstance(reference, dict):
                    reference = {}
                rec = {
                    "issue_id": issue_id,
                    "journal": str(journal),
                    "year": issue_years.get(issue_id),
                    "volume": str(reference.get("volume", "")),
                    "issue": str(reference.get("issue", "")),
                    "official_url": str(reference.get("official_url", "")),
                    "authority": authority,
                    "refreshed_at": str(snapshot.get("refreshed_at", "")),
                    "collector_revision": str(snapshot.get("collector_revision", "")),
                }
                candidate.setdefault(journal, {})[issue_id] = rec
                if kind == "authoritative":
                    authoritative.setdefault(journal, {})[issue_id] = rec
    return candidate, authoritative


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
    for state_path in state_paths:
        states.append(load_state(state_path))

    exclusions = _collect_exclusions(states)
    candidates, authoritative = _collect_discovery(states, window_year)

    def _is_excluded(iid: str) -> bool:
        return iid in exclusions and str(exclusions[iid].get("status", "")) in EXCLUSION_OUT_OF_SET_STATUSES

    records: list[dict[str, Any]] = []
    for key, config in journals.items():
        journal_id = _journal_id(key, config)
        title = str(config.get("name") or key)
        cand_map = candidates.get(key) or candidates.get(key.upper()) or {}
        auth_map = authoritative.get(key) or authoritative.get(key.upper()) or {}
        candidate_ids = sorted(cand_map.keys())
        authoritive_ids = sorted(auth_map.keys())

        # authoritative snapshot for the window?
        has_authoritative = bool(authoritive_ids) or any(
            _authority_kind(str(auth_map[i].get("authority", ""))) == "authoritative" for i in authoritive_ids
        )

        excluded_ids = sorted(i for i in authoritive_ids if _is_excluded(i))
        effective_ids = sorted(i for i in authoritive_ids if not _is_excluded(i))

        # collect authority + refreshedAt for the journal (fall back to candidate
        # discovery so unknown/crossref authority is reported, not blanked)
        if authoritive_ids:
            authority = str(auth_map[authoritive_ids[0]].get("authority", ""))
            refreshed_at = str(auth_map[authoritive_ids[0]].get("refreshed_at", ""))
        elif candidate_ids:
            authority = str(cand_map[candidate_ids[0]].get("authority", ""))
            refreshed_at = str(cand_map[candidate_ids[0]].get("refreshed_at", ""))
        else:
            authority = ""
            refreshed_at = ""

        ready_ids: list[str] = []
        blocked_ids: list[str] = []
        missing_ids: list[str] = []
        for iid in effective_ids:
            rec = auth_map[iid]
            integrity = inspect_archive(
                _archive_path(api_root, config, iid), expected_issue_id=iid, expected_journal_id=journal_id
            )
            if integrity.get("archive_exists"):
                if integrity.get("publication_state") == "ready" and integrity.get("archive_valid"):
                    ready_ids.append(iid)
                else:
                    blocked_ids.append(iid)
            else:
                missing_ids.append(iid)

        # status
        authority_kind = _authority_kind(authority)
        if not has_authoritative or not effective_ids:
            status = "NOT_MEASURED"
            evidence = "no positive authoritative 2026 expected set established"
        else:
            if not missing_ids and not blocked_ids:
                status = "COMPLETE"
                evidence = "all effective authoritative expected 2026 issues publication-ready"
            elif missing_ids:
                status = "PARTIAL"
                evidence = f"{len(missing_ids)} effective expected 2026 issue(s) absent"
            else:
                status = "SOURCE_BLOCKED"
                evidence = "expected set exists but one or more effective issues blocked/source-pending"

        records.append(
            {
                "journalId": journal_id,
                "journalKey": key,
                "title": title,
                "windowStart": window_start,
                "windowEnd": window_end,
                "authority": authority,
                "authorityClassification": authority_kind,
                "evidenceRef": "",
                "discoveryRefreshedAt": refreshed_at,
                "discoveredCandidateIssues": candidate_ids,
                "authoritativeExpectedIssues": authoritive_ids,
                "excludedIssues": [
                    {"issue_id": i, "status": str(exclusions[i].get("status", "")), "reason": str(exclusions[i].get("reason", ""))}
                    for i in excluded_ids
                ],
                "effectiveExpectedIssues": effective_ids,
                "archivedIssues": sorted(set(ready_ids) | set(blocked_ids)),
                "publicationReadyIssues": ready_ids,
                "missingIssues": [
                    {"issue_id": i, "volume": str(auth_map[i].get("volume", "")), "issue": str(auth_map[i].get("issue", "")), "official_url": str(auth_map[i].get("official_url", ""))}
                    for i in missing_ids
                ],
                "sourceBlockedIssues": blocked_ids,
                "expectedIssueCount": len(effective_ids),
                "archivedIssueCount": len(set(ready_ids) | set(blocked_ids)),
                "publicationReadyIssueCount": len(ready_ids),
                "missingIssueCount": len(missing_ids),
                "sourceBlockedIssueCount": len(blocked_ids),
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
        "authoritative_expected_issues_total": sum(len(r["authoritativeExpectedIssues"]) for r in records),
        "effective_expected_issues_total": sum(r["expectedIssueCount"] for r in records),
        "excluded_issues_total": sum(len(r["excludedIssues"]) for r in records),
        "archived_issues_total": sum(r["archivedIssueCount"] for r in records),
        "publication_ready_issues_total": sum(r["publicationReadyIssueCount"] for r in records),
        "confirmed_missing_issues_total": sum(r["missingIssueCount"] for r in records),
        "blocked_issues_total": sum(r["sourceBlockedIssueCount"] for r in records),
    }

    return {
        "schema_version": "1.2",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "windowStart": window_start,
        "windowEnd": window_end,
        "scope": "journals_tracked",
        "daily_door_semantics": {"daily_door_formal_monitor": 97, "journals_tracked": len(journals)},
        "reconciliation": reconciliation,
        "journals": records,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    rec = payload["reconciliation"]
    lines = [
        "# Academic Door 2026 completeness ledger", "", f"Window: {payload['windowStart']} -> {payload['windowEnd']}",
        f"Generated: {payload['generated_at']}", "", "## Summary", "",
        f"- journals tracked: {rec['journal_count']}", f"- COMPLETE: {rec['complete']}",
        f"- PARTIAL: {rec['partial']}", f"- NOT_MEASURED: {rec['not_measured']}",
        f"- SOURCE_BLOCKED: {rec['source_blocked']}",
        f"- authoritative expected issues: {rec['authoritative_expected_issues_total']}",
        f"- excluded issues: {rec['excluded_issues_total']}",
        f"- effective expected issues: {rec['effective_expected_issues_total']}",
        f"- archived issues: {rec['archived_issues_total']}",
        f"- publication-ready issues: {rec['publication_ready_issues_total']}",
        f"- confirmed missing issues: {rec['confirmed_missing_issues_total']}",
        f"- blocked issues: {rec['blocked_issues_total']}", "", "## Per-journal", "",
        "| Journal | Status | Authority | Expected(Eff) | Archived | Ready | Missing | Blocked | Excluded | Evidence |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for record in payload["journals"]:
        evidence = re.sub(r"\s+", " ", str(record.get("evidence", ""))).replace("|", "/")
        lines.append(
            f"| {record['journalKey']} | {record['status']} | {str(record.get('authority')) or '-'} | "
            f"{record['expectedIssueCount']} | {record['archivedIssueCount']} | {record['publicationReadyIssueCount']} | "
            f"{record['missingIssueCount']} | {record['sourceBlockedIssueCount']} | {len(record['excludedIssues'])} | {evidence[:80]} |"
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

