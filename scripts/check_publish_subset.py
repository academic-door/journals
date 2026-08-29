"""Validate an issue-scoped closeout publish before data is pushed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _records(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["issue_id"]): item
        for item in payload.get("records", [])
        if isinstance(item, dict) and item.get("issue_id")
    }


def _status_counts(payload: dict[str, Any]) -> dict[str, int]:
    coverage = payload.get("coverage") or {}
    summary = payload.get("summary") or {}
    return {
        "ready": int(coverage.get("publication_ready", summary.get("complete", 0)) or 0),
        "missing": int(coverage.get("missing", 0) or 0),
        "source_pending": int(coverage.get("source_pending", summary.get("pending", 0)) or 0),
    }


def build_report(
    before_status: dict[str, Any],
    after_status: dict[str, Any],
    before_gap: dict[str, Any],
    after_gap: dict[str, Any],
    publish_issue_ids: list[str],
    translation_model_calls: int,
    translation_cache_reuses: int = 0,
    allow_translation_calls: bool = False,
) -> dict[str, Any]:
    before = _records(before_gap)
    after = _records(after_gap)
    ready_before_ids = {key for key, value in before.items() if value.get("category") == "ready"}
    ready_after_ids = {key for key, value in after.items() if value.get("category") == "ready"}
    publish_ids = sorted({value.strip() for value in publish_issue_ids if value.strip()})
    publishable = [issue_id for issue_id in publish_ids if issue_id in ready_after_ids]
    blocked = [issue_id for issue_id in publish_ids if issue_id not in ready_after_ids]
    regressed = sorted(ready_before_ids - ready_after_ids)
    before_counts = _status_counts(before_status)
    after_counts = _status_counts(after_status)
    errors: list[str] = []
    if not publish_ids:
        errors.append("publish_issue_ids is empty")
    if blocked:
        errors.append("requested issue is not ready after candidate merge")
    if not publishable:
        errors.append("no requested issue became ready")
    if regressed:
        errors.append("existing ready issues regressed")
    if translation_model_calls != 0 and not allow_translation_calls:
        errors.append("A-mode translation_model_calls must be zero")
    return {
        "ok": not errors,
        "errors": errors,
        "publish_issue_ids": publish_ids,
        "publishable_issue_ids": publishable,
        "blocked_issue_ids": blocked,
        "ready_before": before_counts["ready"],
        "ready_after_candidate": after_counts["ready"],
        "missing_before": before_counts["missing"],
        "missing_after_candidate": after_counts["missing"],
        "source_pending_before": before_counts["source_pending"],
        "source_pending_after_candidate": after_counts["source_pending"],
        "regressed_ready_issue_ids": regressed,
        "translation_model_calls": translation_model_calls,
        "translation_cache_reuses": translation_cache_reuses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-status", type=Path, required=True)
    parser.add_argument("--after-status", type=Path, required=True)
    parser.add_argument("--before-gap", type=Path, required=True)
    parser.add_argument("--after-gap", type=Path, required=True)
    parser.add_argument("--publish-issue-ids", required=True)
    parser.add_argument("--translation-model-calls", type=int, default=0)
    parser.add_argument("--translation-cache-reuses", type=int, default=0)
    parser.add_argument("--allow-translation-calls", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        _read(args.before_status),
        _read(args.after_status),
        _read(args.before_gap),
        _read(args.after_gap),
        args.publish_issue_ids.split(","),
        args.translation_model_calls,
        args.translation_cache_reuses,
        args.allow_translation_calls,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
