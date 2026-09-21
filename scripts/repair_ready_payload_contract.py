"""Repair READY public issue payloads to the downstream canonical contract.

This migration is intentionally narrow:
- READY/complete public payloads must explicitly declare development_sample=false
  when the marker is absent;
- when the current READY payload and an archive payload have the same issue_id,
  the current payload is the canonical copy and refreshes the archive.

No article membership, DOI, author, abstract, or source fact is synthesized.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_journals import read_json, validate_issue, write_json


def is_ready_payload(payload: dict[str, Any] | None) -> bool:
    return bool(
        payload
        and payload.get("publication_state") == "ready"
        and payload.get("content_status") == "complete"
    )


def repaired_ready_payload(
    payload: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the deterministic READY-contract form of one payload."""

    if not is_ready_payload(payload):
        return copy.deepcopy(payload)

    repaired = copy.deepcopy(payload)
    if (
        is_ready_payload(current)
        and str(current.get("issue_id", "")).strip()
        == str(payload.get("issue_id", "")).strip()
    ):
        repaired = copy.deepcopy(current)

    # Public canonical production payloads are not development fixtures.
    # Preserve any explicit marker; only make the historical implicit false
    # value explicit for downstream fail-closed consumers.
    if "development_sample" not in repaired:
        repaired["development_sample"] = False
    return repaired


def repair_public_ready_contract(api_root: Path) -> dict[str, Any]:
    journals_root = api_root / "journals"
    changed: list[str] = []

    for journal_dir in sorted(path for path in journals_root.iterdir() if path.is_dir()):
        issues_dir = journal_dir / "issues"
        if not issues_dir.is_dir():
            continue

        current_path = issues_dir / "current.json"
        current = read_json(current_path)
        if current and is_ready_payload(current):
            repaired_current = repaired_ready_payload(current)
            validate_issue(repaired_current)
            if repaired_current != current:
                write_json(current_path, repaired_current)
                current = repaired_current
                changed.append(str(current_path.relative_to(api_root)))
            else:
                current = repaired_current

        for path in sorted(issues_dir.glob("*.json")):
            if path.name in {"current.json", "detected.json", "index.json"}:
                continue
            payload = read_json(path)
            if not payload or not is_ready_payload(payload):
                continue
            repaired = repaired_ready_payload(payload, current=current)
            validate_issue(repaired)
            if repaired != payload:
                write_json(path, repaired)
                changed.append(str(path.relative_to(api_root)))

    return {"changed_count": len(changed), "changed": changed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, default=Path("public/api/v1"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        before: dict[Path, str] = {}
        for path in (args.api_root / "journals").glob("*/issues/*.json"):
            if path.name not in {"detected.json", "index.json"}:
                before[path] = path.read_text(encoding="utf-8")
        report = repair_public_ready_contract(args.api_root)
        for path, content in before.items():
            path.write_text(content, encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["changed_count"] else 0

    report = repair_public_ready_contract(args.api_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
