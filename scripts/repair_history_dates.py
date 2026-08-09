from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.metadata_fallback import MONTHS_BY_ISSUE, _crossref_items, _publication_date
from scripts.update_journals import (
    JOURNALS_PATH,
    PUBLIC_API,
    now_iso,
    read_json,
    update_indexes,
    write_archive_index,
    write_json,
)


DEFAULT_JOURNALS = ("JHE", "JCE", "ENERGY", "ECOLECON")


def repair_journal(
    key: str,
    config: dict[str, Any],
    *,
    session: requests.Session,
    start_year: int,
    check: bool,
) -> tuple[int, list[str]]:
    issue_root = PUBLIC_API / "journals" / config["id"] / "issues"
    archive_paths = sorted(
        path
        for path in issue_root.glob("*.json")
        if path.name not in {"current.json", "detected.json", "index.json"}
    )
    current_path = issue_root / "current.json"
    paths = ([current_path] if current_path.exists() else []) + archive_paths
    if not paths:
        return 0, []

    items = _crossref_items(
        str(config["issn"]),
        session=session,
        timeout=45,
        start_year=start_year,
    )
    changes: list[str] = []
    for path in paths:
        issue = read_json(path)
        if issue is None:
            continue
        repaired = _publication_date(
            str(config["issn"]),
            str(issue.get("volume", "")),
            str(issue.get("issue", "")),
            items,
        )
        current = str(issue.get("publication_date", "")).strip()
        if not repaired or re.fullmatch(r"\d{4}", repaired):
            year_match = re.search(r"\b(20\d{2})\b", current or repaired)
            official_month = MONTHS_BY_ISSUE.get(str(config["issn"]), {}).get(
                str(issue.get("issue", "")),
                "",
            )
            if year_match and official_month:
                repaired = f"{official_month} {year_match.group(1)}"
        if not repaired or repaired == current:
            continue
        changes.append(f"{issue.get('issue_id', path.stem)}: {current} -> {repaired}")
        if not check:
            issue["publication_date"] = repaired
            issue.setdefault("quality", {})[
                "date_source"
            ] = "crossref-volume-filtered"
            write_json(path, issue)

    if changes and archive_paths and not check:
        write_archive_index(
            config["id"],
            config["name"],
            updated_at=now_iso(),
        )
    return len(paths), changes


def main(
    journal_keys: list[str],
    *,
    start_year: int,
    check: bool,
) -> int:
    configs = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))["journals"]
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "AcademicDoorJournals/0.1 "
                "(non-profit academic metadata service; "
                "https://academic-door.github.io/)"
            )
        }
    )
    total_archives = 0
    all_changes: list[str] = []
    for key in journal_keys:
        if key not in configs:
            print(f"FAIL unknown journal code: {key}")
            return 2
        archives, changes = repair_journal(
            key,
            configs[key],
            session=session,
            start_year=start_year,
            check=check,
        )
        total_archives += archives
        all_changes.extend(f"{key} {change}" for change in changes)

    for change in all_changes:
        print(("FAIL" if check else "FIX") + f" {change}")
    if check and all_changes:
        return 1

    if all_changes and not check:
        current_issues = {
            key: issue
            for key, config in configs.items()
            if config.get("enabled")
            and (
                issue := read_json(
                    PUBLIC_API
                    / "journals"
                    / config["id"]
                    / "issues"
                    / "current.json"
                )
            )
            is not None
        }
        update_indexes(
            configs,
            current_issues,
            archive_current=False,
        )
    print(
        "History date repair: "
        f"{total_archives} snapshots checked, {len(all_changes)} changes"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Repair checked-in current and archive dates using Crossref records "
            "filtered by exact volume/issue plus official issue calendars."
        )
    )
    parser.add_argument(
        "--journal",
        action="append",
        dest="journals",
        help="Configured journal code; repeat for multiple journals.",
    )
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report and fail on dates that would change.",
    )
    args = parser.parse_args()
    raise SystemExit(
        main(
            args.journals or list(DEFAULT_JOURNALS),
            start_year=args.start_year,
            check=args.check,
        )
    )
