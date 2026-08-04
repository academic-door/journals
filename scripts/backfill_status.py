"""Render the resumable field-history backfill state as a readable report.

Reads the shared state file (data/backfill-state/field-2025-2026.json) and
writes a machine-readable JSON into the public API tree plus an optional
human-readable Markdown table, so every batch publishes a clickable status.
"""

from __future__ import annotations

import argparse
import json
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


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Field journal history backfill status",
        "",
        f"Updated: {payload['updated_at']}",
        "",
        "| Journal | Issue | Year | Status | Note |",
        "|---|---|---|---|---|",
    ]
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
    parser.add_argument("--state", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default="")
    args = parser.parse_args()

    state = load_state(Path(args.state))
    issues = state.get("issues", {})
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
            }
        )
    payload = {
        "schema_version": "1.0",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": summarize(issues),
        "journals": by_journal,
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
