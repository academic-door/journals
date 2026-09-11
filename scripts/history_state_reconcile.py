"""Reconcile historical state checkpoints from canonical archive/index truth.

This module is deliberately read-only with respect to public archive/index data.
It updates a state row only when the issue archive exists and the journal archive
index independently agrees with the archive's content/source/publication state.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_history import atomic_write_json, inspect_archive, retry_class_for


READINESS_FIELDS = ("content_status", "source_status", "publication_state")


def _load_index(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """Load one archive index fail-closed.

    Returns an empty mapping plus an error marker when the index is missing,
    malformed, or contains duplicate issue ids.
    """

    if not path.exists():
        return {}, "index_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        return {}, f"index_invalid: {type(error).__name__}: {error}"
    rows = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}, "index_invalid: issues_not_list"
    entries: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return {}, "index_invalid: issue_not_object"
        issue_id = str(row.get("issue_id", "")).strip()
        if not issue_id:
            return {}, "index_invalid: issue_id_missing"
        if issue_id in entries:
            return {}, f"index_invalid: duplicate_issue_id:{issue_id}"
        entries[issue_id] = row
    return entries, ""


def _resolve_journal_config(journals: dict[str, Any], journal: str) -> dict[str, Any] | None:
    direct = journals.get(journal)
    if isinstance(direct, dict):
        return direct
    folded = journal.casefold()
    for key, value in journals.items():
        if str(key).casefold() == folded and isinstance(value, dict):
            return value
    return None


def _desired_operational_state(integrity: dict[str, Any]) -> dict[str, str]:
    publication_state = str(integrity.get("publication_state", "blocked"))
    last_error = (
        str(integrity.get("reason", "")) if publication_state == "blocked" else ""
    )
    return {
        "status": publication_state,
        "content_status": str(integrity.get("content_status", "blocked")),
        "source_status": str(integrity.get("source_status", "source_pending")),
        "publication_state": publication_state,
        "last_error": last_error,
        "retry_class": retry_class_for(publication_state, last_error),
    }


def reconcile_state_file(
    state_path: Path,
    *,
    journals: dict[str, Any],
    api_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reconcile stale operational state rows from matching archive/index truth.

    Identity/routing fields, discovery snapshots, and attempt diagnostics are
    preserved. Missing or disagreeing canonical evidence is skipped.
    """

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"state payload is not an object: {state_path}")
    issues = payload.get("issues", {})
    if not isinstance(issues, dict):
        raise ValueError(f"state issues is not an object: {state_path}")

    index_cache: dict[str, tuple[dict[str, dict[str, Any]], str]] = {}
    changes: list[dict[str, Any]] = []

    for issue_id, entry in issues.items():
        if not isinstance(entry, dict):
            continue
        journal = str(entry.get("journal", "")).strip()
        config = _resolve_journal_config(journals, journal)
        if not config:
            continue
        journal_id = str(config.get("id", "")).strip()
        if not journal_id:
            continue

        issue_dir = api_root / "journals" / journal_id / "issues"
        integrity = inspect_archive(
            issue_dir / f"{issue_id}.json",
            expected_issue_id=str(issue_id),
            expected_journal_id=journal_id,
        )
        if not integrity.get("archive_exists"):
            continue

        if journal_id not in index_cache:
            index_cache[journal_id] = _load_index(issue_dir / "index.json")
        index, index_error = index_cache[journal_id]
        if index_error:
            continue
        index_entry = index.get(str(issue_id))
        if not isinstance(index_entry, dict):
            continue
        if any(
            str(index_entry.get(field, "")) != str(integrity.get(field, ""))
            for field in READINESS_FIELDS
        ):
            continue

        desired = _desired_operational_state(integrity)
        before = {field: str(entry.get(field, "")) for field in desired}
        had_next_retry = "next_retry_at" in entry
        if before == desired and not had_next_retry:
            continue

        changes.append(
            {
                "issue_id": str(issue_id),
                "journal": journal,
                "before": before,
                "after": dict(desired),
                "cleared_next_retry_at": had_next_retry,
            }
        )
        if dry_run:
            continue
        entry.update(desired)
        entry.pop("next_retry_at", None)

    if changes and not dry_run:
        payload["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        atomic_write_json(state_path, payload)

    return {
        "state_path": str(state_path),
        "changed_count": len(changes),
        "changes": changes,
        "dry_run": dry_run,
    }


def _load_journals(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("journals"), dict):
        raise ValueError(f"journals config missing journals mapping: {path}")
    return payload["journals"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile historical state checkpoints from canonical archive/index truth."
    )
    parser.add_argument("--state", action="append", required=True, help="State JSON path; repeatable")
    parser.add_argument("--api-root", default=str(ROOT / "public" / "api" / "v1"))
    parser.add_argument(
        "--journals-config", default=str(ROOT / "config" / "journals.yml")
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-json", default="")
    args = parser.parse_args()

    journals = _load_journals(Path(args.journals_config))
    api_root = Path(args.api_root)
    results = [
        reconcile_state_file(
            Path(state), journals=journals, api_root=api_root, dry_run=args.dry_run
        )
        for state in args.state
    ]
    report = {
        "changed_count": sum(int(result["changed_count"]) for result in results),
        "states": results,
        "dry_run": args.dry_run,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_json:
        Path(args.report_json).write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
