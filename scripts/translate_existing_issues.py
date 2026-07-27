"""Idempotently backfill translations into already-collected issue snapshots.

This is a migration/recovery utility, not the scheduled collection entrypoint.
It reuses valid cache entries, requests only missing translations, and may be
run repeatedly without replacing translations that already pass validation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    JOURNALS_PATH,
    TRANSLATION_CACHE,
    apply_translation_cache,
    is_publishable_snapshot,
    load_available_issues,
    public_issue_path,
    read_json,
    update_indexes,
    validate_issue,
    write_json,
)


def translate_existing(key: str, config: dict) -> dict:
    target = public_issue_path(config["id"])
    issue = read_json(target)
    if issue is None:
        return {"journal": key, "result": "missing_issue"}
    report = translate_missing(
        issue,
        TRANSLATION_CACHE / f"{config['id']}.json",
    )
    issue = apply_translation_cache(issue)
    validate_issue(issue)
    if not is_publishable_snapshot(issue):
        return {
            "journal": key,
            "result": "invalid_snapshot",
            "translation": report,
        }
    write_json(target, issue)
    complete = issue["quality"]["translation_complete"]
    total = issue["research_article_count"]
    return {
        "journal": key,
        "result": "complete" if complete == total else "translation_incomplete",
        "translated": complete,
        "articles": total,
        "translation": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Translate already-collected current issues without recrawling."
    )
    parser.add_argument("--journal", default="ALL")
    args = parser.parse_args()

    configs = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))["journals"]
    selected = {
        key: config
        for key, config in configs.items()
        if config.get("enabled")
        and (args.journal == "ALL" or key.casefold() == args.journal.casefold())
    }
    if not selected:
        parser.error(f"Unknown journal: {args.journal}")

    reports = [translate_existing(key, config) for key, config in selected.items()]
    issues = load_available_issues(configs, {})
    update_indexes(configs, issues)
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if all(report["result"] == "complete" for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
