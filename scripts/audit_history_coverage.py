"""Strict four-way audit for history discovery, state, index and archives."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_history import (
    COLLECTOR_REVISION,
    VERIFIED_SOURCE_STATUSES,
    inspect_archive,
)
from scripts.backfill_status import (
    DEFAULT_API_ROOT,
    DEFAULT_JOURNALS_CONFIG,
    discovery_expectations,
    load_journals,
    load_state,
)


def _index_entries(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    if not path.exists():
        return {}, "index_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("issues", [])
        if not isinstance(items, list):
            return {}, "index_issues_not_array"
        return {
            str(item.get("issue_id", "")): item
            for item in items
            if isinstance(item, dict) and item.get("issue_id")
        }, ""
    except Exception as error:
        return {}, f"index_invalid: {type(error).__name__}: {error}"


def audit_history_integrity(
    states: list[dict[str, Any]],
    *,
    journals: dict[str, Any],
    api_root: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    merged_issues: dict[str, Any] = {}
    for state in states:
        issues = state.get("issues", {})
        if isinstance(issues, dict):
            merged_issues.update(issues)
    expected = discovery_expectations(states, merged_issues)

    discovered_journals: set[str] = set()
    for state in states:
        discovery = state.get("discovery", {})
        if not isinstance(discovery, dict):
            continue
        discovered_journals.update(str(key) for key in discovery)
        for journal, snapshot in discovery.items():
            if not isinstance(snapshot, dict):
                errors.append(f"{journal}: discovery snapshot is not an object")
                continue
            if not str(snapshot.get("refreshed_at", "")):
                errors.append(f"{journal}: discovery refreshed_at missing")
            if not str(snapshot.get("authority", "")):
                errors.append(f"{journal}: discovery authority missing")
            if snapshot.get("collector_revision") != COLLECTOR_REVISION:
                errors.append(
                    f"{journal}: stale collector_revision "
                    f"{snapshot.get('collector_revision', '')!r}"
                )
            ids = [str(value) for value in snapshot.get("issue_ids", [])]
            if len(ids) != len(set(ids)):
                errors.append(f"{journal}: duplicate discovery issue ids")
    for journal in journals:
        if journal not in discovered_journals:
            errors.append(f"{journal}: discovery snapshot missing")

    expected_ids = set(expected)
    state_ids = set(merged_issues)
    for issue_id in sorted(expected_ids - state_ids):
        errors.append(f"{issue_id}: discovery has no state entry")
    for issue_id in sorted(state_ids - expected_ids):
        errors.append(f"{issue_id}: state entry absent from discovery snapshot")

    index_cache: dict[str, tuple[dict[str, dict[str, Any]], str]] = {}
    counts = {
        "discovered": len(expected_ids),
        "state": len(expected_ids & state_ids),
        "indexed": 0,
        "archived": 0,
        "publication_ready": 0,
        "source_pending": 0,
    }
    for issue_id, expectation in sorted(expected.items()):
        journal = str(expectation["journal"])
        config = journals.get(journal)
        if config is None:
            errors.append(f"{issue_id}: unknown journal {journal}")
            continue
        journal_id = str(config["id"])
        archive = api_root / "journals" / journal_id / "issues" / f"{issue_id}.json"
        integrity = inspect_archive(
            archive,
            expected_issue_id=issue_id,
            expected_journal_id=journal_id,
        )
        if not integrity.get("archive_exists"):
            errors.append(f"{issue_id}: archive missing")
        else:
            counts["archived"] += 1
        if integrity.get("publication_state") == "ready":
            counts["publication_ready"] += 1
            if integrity.get("source_status") not in VERIFIED_SOURCE_STATUSES:
                errors.append(f"{issue_id}: ready archive has unverified source")
        if integrity.get("source_status") == "source_pending":
            counts["source_pending"] += 1

        entry = merged_issues.get(issue_id, {})
        state_publication = str(
            entry.get("publication_state") or entry.get("status") or ""
        )
        archive_publication = str(integrity.get("publication_state", "blocked"))
        if state_publication == "complete":
            state_publication = "ready"
        if state_publication != archive_publication:
            errors.append(
                f"{issue_id}: state publication {state_publication or 'missing'} "
                f"!= archive {archive_publication}"
            )
        for field in ("content_status", "source_status"):
            state_value = str(entry.get(field, ""))
            archive_value = str(integrity.get(field, ""))
            if state_value != archive_value:
                errors.append(
                    f"{issue_id}: state {field} {state_value or 'missing'} "
                    f"!= archive {archive_value}"
                )

        if journal_id not in index_cache:
            index_cache[journal_id] = _index_entries(
                api_root / "journals" / journal_id / "issues" / "index.json"
            )
        index, index_error = index_cache[journal_id]
        if index_error:
            errors.append(f"{journal}: {index_error}")
            index_cache[journal_id] = (index, "")  # report once per journal
        index_entry = index.get(issue_id)
        if index_entry is None:
            errors.append(f"{issue_id}: archive index entry missing")
        else:
            counts["indexed"] += 1
            for field in ("content_status", "source_status", "publication_state"):
                if str(index_entry.get(field, "")) != str(integrity.get(field, "")):
                    errors.append(
                        f"{issue_id}: index {field} "
                        f"{index_entry.get(field, '') or 'missing'} != archive "
                        f"{integrity.get(field, '')}"
                    )

    return {
        "schema_version": "1.0",
        "status": "pass" if not errors else "fail",
        "counts": counts,
        "error_count": len(errors),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", action="append", required=True)
    parser.add_argument("--api-root", default=str(DEFAULT_API_ROOT))
    parser.add_argument("--journals-config", default=str(DEFAULT_JOURNALS_CONFIG))
    parser.add_argument("--report-json", default="")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = audit_history_integrity(
        [load_state(Path(value)) for value in args.state],
        journals=load_journals(Path(args.journals_config)),
        api_root=Path(args.api_root),
    )
    if args.report_json:
        target = Path(args.report_json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.strict and report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
