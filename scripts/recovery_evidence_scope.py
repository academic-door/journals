"""Resolve the official-evidence issue scope for one historical recovery run."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ISSUE_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")


def _validate(issue_id: str) -> str:
    value = issue_id.strip()
    if not value or not ISSUE_ID_RE.fullmatch(value):
        raise ValueError(f"invalid recovery issue id: {issue_id!r}")
    return value


def scoped_issue_ids(shards_dir: Path, explicit: str = "") -> list[str]:
    values: list[str] = []
    if explicit.strip():
        values.extend(_validate(value) for value in explicit.split(",") if value.strip())
    elif shards_dir.exists():
        for path in sorted(shards_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            issue_ids = payload.get("issue_ids", [])
            if not isinstance(issue_ids, list):
                raise ValueError(f"{path}: issue_ids must be an array")
            values.extend(_validate(str(value)) for value in issue_ids)

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards-dir", type=Path, required=True)
    parser.add_argument("--explicit", default="")
    parser.add_argument("--github-env", type=Path)
    args = parser.parse_args()

    issue_ids = scoped_issue_ids(args.shards_dir, args.explicit)
    csv = ",".join(issue_ids)
    if args.github_env:
        with args.github_env.open("a", encoding="utf-8") as handle:
            handle.write(f"RECOVERY_EVIDENCE_ISSUE_IDS={csv}\n")
    print(json.dumps({"issue_count": len(issue_ids), "issue_ids": issue_ids}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
