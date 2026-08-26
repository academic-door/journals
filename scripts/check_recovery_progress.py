"""Fail a recovery wave that regresses ready data or produces no useful delta."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


UNRESOLVED = {
    "recoverable",
    "translation_required",
    "source_pending",
    "browser_required",
    "blocked",
}


def evaluate_progress(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_records = {
        str(item.get("issue_id", "")): item
        for item in before.get("records", [])
        if isinstance(item, dict) and item.get("issue_id")
    }
    after_records = {
        str(item.get("issue_id", "")): item
        for item in after.get("records", [])
        if isinstance(item, dict) and item.get("issue_id")
    }
    ready_before = {key for key, item in before_records.items() if item.get("category") == "ready"}
    ready_after = {key for key, item in after_records.items() if item.get("category") == "ready"}
    lost_ready = sorted(ready_before - ready_after)
    unresolved_before = sum(item.get("category") in UNRESOLVED for item in before_records.values())
    unresolved_after = sum(item.get("category") in UNRESOLVED for item in after_records.values())
    source_before = sum(item.get("category") == "source_pending" for item in before_records.values())
    source_after = sum(item.get("category") == "source_pending" for item in after_records.values())
    improved_ids: list[str] = []
    for issue_id, current in after_records.items():
        previous = before_records.get(issue_id)
        if not previous:
            continue
        previous_counts = previous.get("counts") or {}
        current_counts = current.get("counts") or {}
        if (
            current.get("category") in {"ready", "excluded_with_official_evidence"}
            and previous.get("category") != current.get("category")
        ) or (
            not previous.get("archive_exists") and current.get("archive_exists")
        ) or any(
            int(current_counts.get(field) or 0) > int(previous_counts.get(field) or 0)
            for field in ("abstract_en", "translation_cn")
        ):
            improved_ids.append(issue_id)
    errors: list[str] = []
    warnings: list[str] = []
    if lost_ready:
        errors.append("ready issues regressed")
    if source_after > source_before:
        warnings.append(
            "source_pending increased because a previously missing archive became explicit"
        )
    if unresolved_after > unresolved_before:
        errors.append("unresolved issue count increased")
    if not improved_ids and ready_after == ready_before and unresolved_after == unresolved_before:
        errors.append("wave produced no measurable progress")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "ready_before": len(ready_before),
        "ready_after": len(ready_after),
        "unresolved_before": unresolved_before,
        "unresolved_after": unresolved_after,
        "source_pending_before": source_before,
        "source_pending_after": source_after,
        "new_ready_issue_ids": sorted(ready_after - ready_before),
        "improved_issue_ids": sorted(improved_ids),
        "lost_ready_issue_ids": lost_ready,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_progress(
        json.loads(args.before.read_text(encoding="utf-8")),
        json.loads(args.after.read_text(encoding="utf-8")),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
