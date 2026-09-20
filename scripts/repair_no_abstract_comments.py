"""Repair historical comments that legitimately have no source abstract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from collectors.article_types import normalize_no_abstract_comment


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_ROOT = ROOT / "public" / "api" / "v1"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def repair(api_root: Path, *, check: bool) -> tuple[int, list[str]]:
    changed_files = 0
    changes: list[str] = []
    for path in sorted((api_root / "journals").glob("*/issues/*.json")):
        if path.name in {"index.json", "detected.json"}:
            continue
        issue = _read(path)
        issue_changes: list[str] = []
        for article in issue.get("articles", []):
            if not isinstance(article, dict):
                continue
            if normalize_no_abstract_comment(article):
                issue_changes.append(
                    str(article.get("doi") or article.get("paper_id") or "unknown")
                )
        if not issue_changes:
            continue
        changed_files += 1
        changes.extend(
            f"{issue.get('issue_id', path.stem)}:{item}" for item in issue_changes
        )
        if not check:
            path.write_text(
                json.dumps(issue, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return changed_files, changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, default=DEFAULT_API_ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files, changes = repair(args.api_root, check=args.check)
    for change in changes:
        print(("FAIL" if args.check else "FIX") + f" {change}")
    print(f"No-abstract comment repair: {files} files, {len(changes)} articles")
    return 1 if args.check and changes else 0


if __name__ == "__main__":
    raise SystemExit(main())
