from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_journals import (
    JOURNALS_PATH,
    normalize_issue_content,
    public_issue_path,
    read_json,
    update_indexes,
    validate_issue,
    write_json,
)


def load_normalized_issues() -> tuple[
    dict[str, dict[str, Any]],
    list[str],
    list[str],
]:
    config = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))["journals"]
    issues: dict[str, dict[str, Any]] = {}
    changed: list[str] = []
    missing: list[str] = []
    for key, journal in config.items():
        if not journal.get("enabled"):
            continue
        path = public_issue_path(journal["id"])
        issue = read_json(path)
        if issue is None:
            missing.append(journal["id"])
            continue
        normalized = normalize_issue_content(copy.deepcopy(issue))
        validate_issue(normalized)
        if normalized != issue:
            changed.append(journal["id"])
        issues[key] = normalized
    return issues, changed, missing


def main(*, check: bool = False) -> int:
    config = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))["journals"]
    enabled = [journal for journal in config.values() if journal.get("enabled")]
    issues, changed, missing = load_normalized_issues()
    if missing:
        for journal_id in missing:
            print(f"FAIL {journal_id}: current issue JSON is missing")
        return 1
    if check:
        if changed:
            print(
                "FAIL public snapshots require deterministic normalization: "
                + ", ".join(changed)
            )
            return 1
        print(f"public snapshot normalization: {len(issues)}/{len(enabled)} clean")
        return 0

    for key, issue in issues.items():
        write_json(public_issue_path(config[key]["id"]), issue)
    update_indexes(config, issues, archive_current=False)
    print(
        "public snapshot rebuilt: "
        f"{len(issues)}/{len(enabled)} journals; "
        f"{len(changed)} normalized"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Normalize the checked-in current issues and rebuild aggregate "
            "indexes without network access or archive side effects."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when a current snapshot is not deterministically normalized.",
    )
    raise SystemExit(main(check=parser.parse_args().check))
