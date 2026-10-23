"""Restore missing historical operational entries from concordant canonical evidence.

Not a publisher observation: authoritative discovery establishes expected identity,
while independently agreeing READY issue archive plus index establish readiness.
No attempt count, first-seen time, or source observation timestamp is synthesized.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from scripts.backfill_history import (
    VERIFIED_SOURCE_STATUSES,
    atomic_write_json,
    inspect_archive,
    retry_class_for,
)
from scripts.history_state_reconcile import _load_index, reconcile_state_file


QUALIFIED_DISCOVERY_AUTHORITY = frozenset(
    {"official_archive", "official_archive_snapshot"}
)
READINESS_FIELDS = ("content_status", "source_status", "publication_state")


def reconcile_missing_ready(
    state_paths: list[Path],
    *,
    journals: dict[str, Any],
    api_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Insert only otherwise-absent READY entries with exact evidence agreement.

    Ambiguous multiple discovery owners fail closed. Preexisting operational
    rows are never replaced here; reconcile_state_file handles them separately.
    """
    states = {path: json.loads(path.read_text(encoding="utf-8")) for path in state_paths}
    existing = {
        str(issue_id)
        for payload in states.values()
        for issue_id in (payload.get("issues") or {})
    }
    discovery: dict[str, list[tuple[Path, str, dict[str, Any]]]] = defaultdict(list)
    for path, payload in states.items():
        for journal, snapshot in (payload.get("discovery") or {}).items():
            if not isinstance(snapshot, dict):
                continue
            for issue_id in snapshot.get("issue_ids", []):
                discovery[str(issue_id)].append((path, str(journal), snapshot))

    changes: list[dict[str, str]] = []
    skipped: dict[str, str] = {}
    index_cache: dict[str, tuple[dict[str, dict[str, Any]], str]] = {}
    touched: set[Path] = set()
    for issue_id in sorted(set(discovery) - existing):
        candidates = discovery[issue_id]
        if len(candidates) != 1:
            skipped[issue_id] = "ambiguous_discovery_owner"
            continue
        path, journal, snapshot = candidates[0]
        config = journals.get(journal)
        if not isinstance(config, dict):
            skipped[issue_id] = "unconfigured_journal"
            continue
        journal_id = str(config.get("id", ""))
        refs = snapshot.get("issue_refs") or {}
        ref = refs.get(issue_id, {}) if isinstance(refs, dict) else {}
        if not isinstance(ref, dict):
            skipped[issue_id] = "invalid_discovery_identity"
            continue
        official_url = str(ref.get("official_url", "")).strip()
        parsed = urlparse(official_url)
        year = (snapshot.get("issue_years") or {}).get(issue_id) or ref.get("year")
        volume = str(ref.get("volume", "")).strip()
        issue = str(ref.get("issue", "")).strip()
        if (
            snapshot.get("authority") not in QUALIFIED_DISCOVERY_AUTHORITY
            or not str(snapshot.get("refreshed_at", "")).strip()
            or ref.get("journal", journal) != journal
            or not journal_id
            or not year
            or not volume
            or not issue
            or parsed.scheme != "https"
            or not parsed.hostname
        ):
            skipped[issue_id] = "unqualified_discovery_identity"
            continue
        issue_dir = api_root / "journals" / journal_id / "issues"
        integrity = inspect_archive(
            issue_dir / f"{issue_id}.json",
            expected_issue_id=issue_id,
            expected_journal_id=journal_id,
        )
        if (
            not integrity.get("archive_exists")
            or integrity.get("publication_state") != "ready"
            or integrity.get("content_status") != "complete"
            or integrity.get("source_status") not in VERIFIED_SOURCE_STATUSES
        ):
            skipped[issue_id] = "archive_not_qualified_ready"
            continue
        if journal_id not in index_cache:
            index_cache[journal_id] = _load_index(issue_dir / "index.json")
        rows, index_error = index_cache[journal_id]
        indexed = rows.get(issue_id)
        if (
            index_error
            or not isinstance(indexed, dict)
            or any(
                str(indexed.get(field, "")) != str(integrity.get(field, ""))
                for field in READINESS_FIELDS
            )
        ):
            skipped[issue_id] = "archive_index_disagree"
            continue

        record = {
            "journal": journal,
            "year": year,
            "volume": volume,
            "issue": issue,
            "official_url": official_url,
            "status": "ready",
            "content_status": "complete",
            "source_status": str(integrity["source_status"]),
            "publication_state": "ready",
            "last_error": "",
            "retry_class": retry_class_for("ready", ""),
            "operational_state_recovered_from": "canonical_archive_and_index",
        }
        changes.append(
            {"issue_id": issue_id, "state_path": str(path), "source_status": record["source_status"]}
        )
        if dry_run:
            continue
        states[path].setdefault("issues", {})[issue_id] = record
        touched.add(path)

    if not dry_run:
        for path in sorted(touched):
            states[path]["updated_at"] = datetime.now(timezone.utc).replace(
                microsecond=0
            ).isoformat()  # State-bookkeeping time, NOT publisher observation time.
            atomic_write_json(path, states[path])
    return {
        "created_count": len(changes),
        "created": changes,
        "skipped": skipped,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", action="append", required=True)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--journals-config", type=Path, default=Path("config/journals.yml"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args()
    journals = yaml.safe_load(args.journals_config.read_text(encoding="utf-8"))["journals"]
    state_paths = [Path(path) for path in args.state]
    new = reconcile_missing_ready(
        state_paths, journals=journals, api_root=args.api_root, dry_run=args.dry_run
    )
    old = [
        reconcile_state_file(
            path, journals=journals, api_root=args.api_root, dry_run=args.dry_run
        )
        for path in state_paths
    ]
    report = {
        "created_count": new["created_count"],
        "created": new["created"],
        "skipped": new["skipped"],
        "updated_count": sum(row["changed_count"] for row in old),
        "updated": [change for row in old for change in row["changes"]],
        "dry_run": args.dry_run,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
