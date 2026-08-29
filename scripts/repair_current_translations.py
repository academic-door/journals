"""Repair only invalid translations in existing current issue snapshots."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    apply_translation_cache,
    normalize_issue_content,
    read_json,
    validated_translation_count,
    write_json,
)


def repair_current_issue(
    issue: dict[str, Any],
    *,
    cache_path: Path,
    token: str | None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Repair one issue without recollecting its official source."""

    required = int(issue.get("research_article_count", 0))
    before = validated_translation_count(issue)
    if before >= required:
        return {
            "issue_id": issue.get("issue_id", ""),
            "status": "unchanged",
            "validated_before": before,
            "validated_after": before,
            "translation_calls": 0,
        }

    translation_report = translate_missing(
        issue,
        cache_path,
        token=token,
    )
    apply_translation_cache(issue, cache_path=cache_path)
    normalize_issue_content(issue)
    after = validated_translation_count(issue)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(translation_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "issue_id": issue.get("issue_id", ""),
        "status": "repaired" if after > before else "unresolved",
        "validated_before": before,
        "validated_after": after,
        "translation_calls": int(translation_report.get("translated", 0)),
        "failed": translation_report.get("failed", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, default=Path("public/api/v1"))
    parser.add_argument(
        "--translation-cache-root",
        type=Path,
        default=Path("data/translation-cache"),
    )
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args()

    config = yaml.safe_load(
        Path("config/journals.yml").read_text(encoding="utf-8")
    )["journals"]
    reports: list[dict[str, Any]] = []
    for journal in config.values():
        if not journal.get("enabled"):
            continue
        issue = read_json(
            args.api_root / "journals" / journal["id"] / "issues" / "current.json"
        )
        if issue is None:
            continue
        result = repair_current_issue(
            issue,
            cache_path=args.translation_cache_root / f"{journal['id']}.json",
            token=os.environ.get("GITHUB_TOKEN"),
            report_path=None,
        )
        reports.append(result)
        if result["status"] != "unchanged":
            write_json(
                args.api_root
                / "journals"
                / journal["id"]
                / "issues"
                / "current.json",
                normalize_issue_content(issue),
            )

    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(reports, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
