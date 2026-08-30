"""Validate an exact source-wave request before any collector starts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def validate_wave(
    manifest: dict[str, Any],
    *,
    issue_ids: list[str],
    categories: list[str],
    publisher: str = "",
    collector: str = "",
    strategy_status: str = "unvalidated",
    state_restore: bool = False,
    source_run_id: str = "",
) -> dict[str, Any]:
    requested = sorted({value.strip() for value in issue_ids if value.strip()})
    allowed_categories = {value.strip() for value in categories if value.strip()}
    records = [item for item in manifest.get("records", []) if isinstance(item, dict)]
    matched = sorted(
        {
            str(item.get("issue_id", "")).strip()
            for item in records
            if str(item.get("issue_id", "")).strip() in requested
            and str(item.get("category", "")).strip() in allowed_categories
        }
    )
    requested_set = set(requested)
    matched_set = set(matched)
    result = {
        "requested_issue_ids": requested,
        "requested_count": len(requested),
        "categories": sorted(allowed_categories),
        "matched_issue_ids": matched,
        "matched_count": len(matched),
        "unmatched_issue_ids": sorted(requested_set - matched_set),
        "publisher": publisher,
        "collector_strategy": collector,
        "strategy_status": strategy_status,
        "state_restore": bool(state_restore),
        "source_run_id": source_run_id,
        "evidence_issue_ids": requested,
        "hard_gate": {
            "matched_count_equals_requested_count": len(matched) == len(requested),
            "sets_equal": matched_set == requested_set,
        },
    }
    if not result["hard_gate"]["matched_count_equals_requested_count"] or not result["hard_gate"]["sets_equal"]:
        raise ValueError(
            "source wave preflight failed: requested issue IDs do not exactly match manifest records"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--issue-ids", required=True)
    parser.add_argument("--categories", required=True)
    parser.add_argument("--publisher", default="")
    parser.add_argument("--collector", default="")
    parser.add_argument("--strategy-status", default="unvalidated")
    parser.add_argument("--state-restore", action="store_true")
    parser.add_argument("--source-run-id", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_wave(
        json.loads(args.manifest.read_text(encoding="utf-8")),
        issue_ids=args.issue_ids.split(","),
        categories=args.categories.split(","),
        publisher=args.publisher,
        collector=args.collector,
        strategy_status=args.strategy_status,
        state_restore=args.state_restore,
        source_run_id=args.source_run_id,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
