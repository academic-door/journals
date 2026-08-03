"""Backfill validated historical issues without changing the current snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.history import HistoricalIssue, discover_official_issues
from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    JOURNALS_PATH,
    PUBLIC_API,
    TRANSLATION_CACHE,
    apply_translation_cache,
    archive_issue,
    is_archivable_snapshot,
    load_available_issues,
    normalize_issue_content,
    now_iso,
    update_indexes,
    validate_issue,
    write_archive_index,
)


HISTORY_CONFIG = ROOT / "config" / "top5-history.yml"
STATE_PATH = ROOT / "data" / "backfill-state" / "top5-2025-2026.json"
STAGING_ROOT = ROOT / "data" / "backfill-staging"


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_json(path: Path, default: object) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def collector_for_issue(
    journal_config: dict[str, Any], issue_ref: HistoricalIssue | str
) -> Callable[[], dict[str, Any]]:
    collector = journal_config["collector"]
    issue_url = (
        issue_ref.official_url if isinstance(issue_ref, HistoricalIssue) else str(issue_ref)
    )
    if collector == "aea":
        from collectors.aea import fetch_current_issue

        return lambda: fetch_current_issue(
            issue_url,
            journal_id=journal_config["id"],
            journal_name=journal_config["name"],
        )
    if collector == "chicago":
        if (
            isinstance(issue_ref, HistoricalIssue)
            and journal_config.get("fallback") == "crossref-repec"
        ):
            from collectors.metadata_fallback import fetch_repec_history_issue

            return lambda: fetch_repec_history_issue(
                journal_id=journal_config["id"],
                journal_name=journal_config["name"],
                issn=str(journal_config["issn"]),
                volume=issue_ref.volume,
                issue=issue_ref.issue,
                repec_series_code=journal_config.get(
                    "repec_series_code", "ucp/jpolec"
                ),
            )
        from collectors.chicago import fetch_current_issue

        return lambda: fetch_current_issue(
            issue_url,
            journal_id=journal_config["id"],
            journal_name=journal_config["name"],
        )
    if collector == "oup":
        if (
            isinstance(issue_ref, HistoricalIssue)
            and journal_config.get("fallback") == "crossref"
        ):
            from collectors.metadata_fallback import fetch_crossref_current_issue

            return lambda: fetch_crossref_current_issue(
                journal_id=journal_config["id"],
                journal_name=journal_config["name"],
                issn=str(journal_config["issn"]),
                current_issue_url=issue_url,
                target_volume=issue_ref.volume,
                target_issue=issue_ref.issue,
                start_year=int(issue_ref.year) - 1,
            )
        from collectors.oup import fetch_current_issue

        return lambda: fetch_current_issue(journal_config["id"], issue_url)
    if collector == "wiley":
        if isinstance(issue_ref, HistoricalIssue) and journal_config.get(
            "repec_series_code"
        ):
            from collectors.metadata_fallback import fetch_repec_history_issue

            return lambda: fetch_repec_history_issue(
                journal_id=journal_config["id"],
                journal_name=journal_config["name"],
                issn=str(journal_config["issn"]),
                volume=issue_ref.volume,
                issue=issue_ref.issue,
                repec_series_code=journal_config["repec_series_code"],
            )
        from collectors.wiley import fetch_current_issue

        return lambda: fetch_current_issue(
            issue_url,
            journal_id=journal_config["id"],
            journal_name=journal_config["name"],
            expected_volume=(
                issue_ref.volume if isinstance(issue_ref, HistoricalIssue) else ""
            ),
            expected_issue=(
                issue_ref.issue if isinstance(issue_ref, HistoricalIssue) else ""
            ),
        )
    if collector == "elsevier":
        if not isinstance(issue_ref, HistoricalIssue):
            raise ValueError("Elsevier history requires a volume/issue reference")
        from collectors.metadata_fallback import fetch_crossref_current_issue

        return lambda: fetch_crossref_current_issue(
            journal_id=journal_config["id"],
            journal_name=journal_config["name"],
            issn=str(journal_config["issn"]),
            current_issue_url=issue_url,
            target_volume=issue_ref.volume,
            target_issue="",
            output_issue="C",
            start_year=int(issue_ref.year) - 2,
        )
    raise ValueError(f"Historical backfill is not configured for {collector}")


def staging_path(issue: HistoricalIssue) -> Path:
    return STAGING_ROOT / issue.journal.casefold() / f"{issue.issue_id}.json"


def update_state(
    state: dict[str, Any],
    issue: HistoricalIssue,
    *,
    status: str,
    error: str = "",
) -> None:
    state.setdefault("schema_version", "1.0")
    state.setdefault("issues", {})[issue.issue_id] = {
        "journal": issue.journal,
        "year": issue.year,
        "volume": issue.volume,
        "issue": issue.issue,
        "official_url": issue.official_url,
        "status": status,
        "last_error": error,
    }
    atomic_write_json(STATE_PATH, state)


def collect_or_resume(
    issue_ref: HistoricalIssue,
    journal_config: dict[str, Any],
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    path = staging_path(issue_ref)
    if path.exists() and not refresh:
        return load_json(path, {})
    issue = collector_for_issue(journal_config, issue_ref)()
    if (
        str(issue.get("volume", "")) != issue_ref.volume
        or str(issue.get("issue", "")) != issue_ref.issue
    ):
        raise ValueError(
            f"Official page mismatch: expected {issue_ref.volume}/{issue_ref.issue}, "
            f"received {issue.get('volume')}/{issue.get('issue')}"
        )
    issue = normalize_issue_content(apply_translation_cache(issue))
    validate_issue(issue)
    atomic_write_json(path, issue)
    return issue


def run_issue(
    issue_ref: HistoricalIssue,
    journal_config: dict[str, Any],
    state: dict[str, Any],
    *,
    translate: bool,
    max_translations: int,
) -> dict[str, Any]:
    current_status = state.get("issues", {}).get(issue_ref.issue_id, {}).get("status")
    archive_path = (
        PUBLIC_API
        / "journals"
        / journal_config["id"]
        / "issues"
        / f"{issue_ref.issue_id}.json"
    )
    if current_status == "complete" and archive_path.exists():
        return {"issue_id": issue_ref.issue_id, "result": "already_complete"}
    if archive_path.exists():
        update_state(state, issue_ref, status="complete")
        return {"issue_id": issue_ref.issue_id, "result": "already_archived"}
    try:
        issue = collect_or_resume(
            issue_ref,
            journal_config,
            refresh=current_status != "complete",
        )
        update_state(state, issue_ref, status="collected")
        issue = apply_translation_cache(issue)
        translation_report = None
        if translate:
            translation_report = translate_missing(
                issue,
                TRANSLATION_CACHE / f"{journal_config['id']}.json",
                max_translations=max_translations,
            )
            issue = apply_translation_cache(issue)
        issue = normalize_issue_content(issue)
        validate_issue(issue)
        atomic_write_json(staging_path(issue_ref), issue)
        if not is_archivable_snapshot(issue):
            update_state(state, issue_ref, status="translation_partial")
            return {
                "issue_id": issue_ref.issue_id,
                "result": "translation_partial",
                "translation": translation_report,
            }
        target = archive_issue(issue)
        if target is None:
            raise ValueError("Historical issue failed archive publication gate")
        update_state(state, issue_ref, status="complete")
        return {
            "issue_id": issue_ref.issue_id,
            "result": "complete",
            "articles": issue["research_article_count"],
            "translation": translation_report,
        }
    except Exception as error:
        update_state(
            state,
            issue_ref,
            status="blocked",
            error=f"{type(error).__name__}: {error}",
        )
        return {
            "issue_id": issue_ref.issue_id,
            "result": "blocked",
            "error": f"{type(error).__name__}: {error}",
        }


def main() -> int:
    global STATE_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=str(HISTORY_CONFIG),
        help="history config yaml (default top5-history.yml)",
    )
    parser.add_argument(
        "--state",
        default=str(STATE_PATH),
        help="resumable state json path",
    )
    parser.add_argument("--journal", default="ALL", help="journal key or ALL")
    parser.add_argument("--from-year", type=int, default=2025)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--max-issues", type=int, default=1)
    parser.add_argument("--max-translations", type=int, default=50)
    args = parser.parse_args()
    STATE_PATH = Path(args.state)
    history_path = Path(args.config)
    history = yaml.safe_load(history_path.read_text(encoding="utf-8"))
    journals = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))["journals"]
    if args.journal != "ALL" and args.journal not in history["journals"]:
        parser.error(f"unknown journal key: {args.journal}")
    years = range(args.from_year, args.to_year + 1)
    selected = [
        key for key in history["journals"] if args.journal == "ALL" or key == args.journal
    ]
    plan: list[HistoricalIssue] = []
    for key in selected:
        plan.extend(
            discover_official_issues(key, history["journals"][key], years=years)
        )
    if args.plan_only:
        print(
            json.dumps(
                [issue.__dict__ for issue in plan],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    state_path = Path(args.state)
    state = load_json(state_path, {"schema_version": "1.0", "issues": {}})
    pending = [
        issue
        for issue in plan
        if state.get("issues", {}).get(issue.issue_id, {}).get("status") != "complete"
    ][: args.max_issues]
    reports: list[dict[str, Any]] = []
    remaining_translations = args.max_translations
    for issue in pending:
        if args.translate and remaining_translations <= 0:
            break
        report = run_issue(
            issue,
            journals[issue.journal],
            state,
            translate=args.translate,
            max_translations=remaining_translations,
        )
        reports.append(report)
        translation = report.get("translation") or {}
        remaining_translations -= int(translation.get("translated", 0))
    for key in selected:
        write_archive_index(
            journals[key]["id"],
            journals[key]["name"],
            updated_at=now_iso(),
        )
    update_indexes(journals, load_available_issues(journals, {}))
    print(
        json.dumps(
            {
                "results": reports,
                "remaining_translation_budget": remaining_translations,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if any(report["result"] == "blocked" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
