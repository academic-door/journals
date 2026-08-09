"""Render resumable backfill state files as readable reports.

Reads one or more resumable state files (data/backfill-state/field-*.json) and
writes a machine-readable JSON into the public API tree plus an optional
human-readable Markdown table. The JSON keeps the legacy ``summary`` and
``journals`` views and adds per-period and per-year breakdowns so the status
page can show 2023-2024 vs 2025-2026 coverage side by side.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1.0", "issues": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: state must be a JSON object")
    return payload


def summarize(issues: dict[str, Any]) -> dict[str, int]:
    counts = {
        "complete": 0,
        "translation_partial": 0,
        "blocked": 0,
        "collected": 0,
        "pending": 0,
    }
    for entry in issues.values():
        status = str(entry.get("status", "") or "pending")
        counts[status if status in counts else "pending"] += 1
    return counts


def period_label(path: Path) -> str:
    match = re.search(r"(\d{4})-(\d{4})", path.name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return path.stem


def group_by_journal(issues: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_journal: dict[str, list[dict[str, Any]]] = {}
    for issue_id, entry in sorted(issues.items()):
        journal = str(entry.get("journal", "?"))
        by_journal.setdefault(journal, []).append(
            {
                "issue_id": issue_id,
                "year": entry.get("year"),
                "volume": entry.get("volume"),
                "status": str(entry.get("status", "") or "pending"),
                "last_error": str(entry.get("last_error", "")),
                "retry_class": entry.get("retry_class", ""),
            }
        )
    return by_journal


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Field journal history backfill status",
        "",
        f"Updated: {payload['updated_at']}",
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
                f"{item['status']} | {note} |"
            )
    return "\n".join(lines) + "\n"


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
    args = parser.parse_args()

    merged_issues: dict[str, Any] = {}
    periods: dict[str, dict[str, Any]] = {}
    for state_arg in args.state:
        state_path = Path(state_arg)
        state = load_state(state_path)
        issues = state.get("issues", {})
        label = period_label(state_path)
        periods[label] = {
            "summary": summarize(issues),
            "issue_count": len(issues),
            "updated_at": str(state.get("updated_at", "")),
        }
        for issue_id, entry in issues.items():
            # Later state files win on conflicts; issue ids are unique per
            # journal/volume/issue, so cross-period collisions are impossible.
            merged_issues[issue_id] = entry

    years: dict[str, dict[str, Any]] = {}
    for entry in merged_issues.values():
        year = str(entry.get("year") or "")
        if year:
            years.setdefault(year, {"issues": {}})["issues"][
                str(entry.get("journal", "?")) + ":" + str(entry.get("volume", ""))
            ] = entry
    year_summaries = {
        year: {
            "summary": summarize(bucket["issues"]),
            "issue_count": len(bucket["issues"]),
            "journals": sorted(
                {entry.get("journal", "?") for entry in bucket["issues"].values()}
            ),
        }
        for year, bucket in sorted(years.items())
    }

    payload = {
        "schema_version": "1.1",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": summarize(merged_issues),
        "journals": group_by_journal(merged_issues),
        "periods": periods,
        "years": year_summaries,
    }
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
