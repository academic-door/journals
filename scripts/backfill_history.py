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
                target_issue=(
                    issue_ref.issue if issue_ref.issue != "c" else ""
                ),
                output_issue=issue_ref.issue.upper(),
                start_year=int(issue_ref.year) - 2,
            )
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
    if collector in ("wiley", "repec"):
        if isinstance(issue_ref, HistoricalIssue) and journal_config.get(
            "repec_series_code"
        ):
            from collectors.metadata_fallback import (
                fetch_crossref_current_issue,
                fetch_repec_history_issue,
            )

            def _collect_repec() -> dict[str, Any]:
                try:
                    return fetch_repec_history_issue(
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                        issn=str(journal_config["issn"]),
                        volume=issue_ref.volume,
                        issue=issue_ref.issue,
                        repec_series_code=journal_config["repec_series_code"],
                    )
                except Exception:
                    # RePEc may not index the volume under the same
                    # volume/issue split; fall back to the Crossref roster.
                    return fetch_crossref_current_issue(
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                        issn=str(journal_config["issn"]),
                        current_issue_url=issue_url,
                        target_volume=issue_ref.volume,
                        target_issue=(
                            issue_ref.issue
                            if issue_ref.issue != "c"
                            else ""
                        ),
                        output_issue=issue_ref.issue.upper(),
                        start_year=int(issue_ref.year) - 2,
                    )

            return _collect_repec
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
        from collectors.elsevier import fetch_elsevier_repec_history_issue
        from collectors.metadata_fallback import fetch_crossref_current_issue

        def _collect() -> dict[str, Any]:
            try:
                return fetch_elsevier_repec_history_issue(
                    journal_id=journal_config["id"],
                    journal_name=journal_config["name"],
                    issn=str(journal_config["issn"]),
                    volume=issue_ref.volume,
                    repec_series_url=journal_config.get("repec_series_url", ""),
                    doi_template=journal_config.get("doi_template", ""),
                )
            except Exception:
                # RePEc archive may not cover the volume; try the official
                # ScienceDirect Search API roster first, then Crossref.
                from collectors.metadata_fallback import (
                    fetch_elsevier_issue_via_search,
                )

                try:
                    return fetch_elsevier_issue_via_search(
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                        issn=str(journal_config["issn"]),
                        volume=issue_ref.volume,
                        issue=issue_ref.issue,
                        official_issue_url=issue_url,
                    )
                except Exception:
                    return fetch_crossref_current_issue(
                        journal_id=journal_config["id"],
                        journal_name=journal_config["name"],
                        issn=str(journal_config["issn"]),
                        current_issue_url=issue_url,
                        target_volume=issue_ref.volume,
                        target_issue="",
                        output_issue="C",
                        start_year=int(issue_ref.year) - 2,
                    )

        return _collect
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


def _is_browser_captured_staging(path: Path) -> bool:
    """A browser-authorized snapshot is authoritative for the official roster;
    refreshing would replace it with an incomplete RePEc/Crossref list."""
    if not path.exists():
        return False
    try:
        issue = load_json(path, {})
    except Exception:
        return False
    quality = issue.get("quality", {}) or {}
    if quality.get("browser_capture") or quality.get(
        "browser_authorized_abstracts"
    ):
        return True
    roster = str(quality.get("roster_transport", "")).lower()
    return "browser" in roster or "browser-authorized" in roster


def collect_or_resume(
    issue_ref: HistoricalIssue,
    journal_config: dict[str, Any],
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    path = staging_path(issue_ref)
    if path.exists() and (not refresh or _is_browser_captured_staging(path)):
        return load_json(path, {})
    issue = collector_for_issue(journal_config, issue_ref)()
    if (
        str(issue.get("volume", "")).casefold() != str(issue_ref.volume).casefold()
        or str(issue.get("issue", "")).casefold()
        != str(issue_ref.issue).casefold()
    ):
        raise ValueError(
            f"Official page mismatch: expected {issue_ref.volume}/{issue_ref.issue}, "
            f"received {issue.get('volume')}/{issue.get('issue')}"
        )
    issue = normalize_issue_content(apply_translation_cache(issue))
    validate_issue(issue)
    atomic_write_json(path, issue)
    return issue


def history_completeness_block(
    issue: dict[str, Any],
    journal_config: dict[str, Any],
    public_api: Path = PUBLIC_API,
) -> str:
    """Return a block reason when a history volume looks implausibly small.

    Elsevier continuous volumes are sometimes thin in Crossref/OpenAlex. Never
    publish a historical issue that is under half the journal's current issue
    size; keep it staged for a manual or browser-authorized capture instead.
    """

    quality = issue.get("quality") or {}
    if quality.get("browser_capture") or "browser-authorized" in str(
        quality.get("roster_transport", "")
    ):
        # A browser-authorized snapshot is the official publisher roster;
        # comparing it against the current issue size would wrongly block
        # genuinely small historical volumes.
        return ""

    collected_count = int(issue.get("research_article_count", 0))
    repec_count = int(quality.get("repec_item_count", 0))
    if repec_count >= 5:
        # RePEc mirrors the publisher's per-volume list, so a volume that
        # collected about as many articles as RePEc lists is genuinely that
        # size (JDE 173 is a real ~12-article issue, not a data gap).
        if collected_count < repec_count * 0.8:
            return (
                f"possible_incomplete_volume: {collected_count} articles "
                f"collected vs RePEc volume {repec_count}; "
                "needs official page or browser-authorized capture"
            )
        return ""
    current_path = (
        public_api / "journals" / journal_config["id"] / "issues" / "current.json"
    )
    reference_count = 0
    if current_path.exists():
        try:
            reference_count = int(
                load_json(current_path, {}).get("research_article_count", 0)
            )
        except (TypeError, ValueError):
            reference_count = 0
    if reference_count >= 10 and collected_count < reference_count * 0.5:
        return (
            f"possible_incomplete_volume: {collected_count} articles "
            f"collected vs current issue {reference_count}; "
            "needs official page or browser-authorized capture"
        )
    return ""


def run_issue(
    issue_ref: HistoricalIssue,
    journal_config: dict[str, Any],
    state: dict[str, Any],
    *,
    translate: bool,
    max_translations: int,
) -> dict[str, Any]:
    current_status = state.get("issues", {}).get(issue_ref.issue_id, {}).get("status")
    current_error = (
        state.get("issues", {}).get(issue_ref.issue_id, {}).get("last_error", "")
    )
    if (
        current_status == "blocked"
        and "possible_incomplete_volume" in current_error
        and not _is_browser_captured_staging(staging_path(issue_ref))
    ):
        return {
            "issue_id": issue_ref.issue_id,
            "result": "blocked",
            "error": (
                "possible_incomplete_volume (skipped: needs official page or "
                "browser-authorized capture)"
            ),
        }
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
        block_reason = history_completeness_block(issue, journal_config)
        if block_reason:
            update_state(state, issue_ref, status="blocked", error=block_reason)
            return {
                "issue_id": issue_ref.issue_id,
                "result": "blocked",
                "error": "possible_incomplete_volume",
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
    parser.add_argument(
        "--report-json",
        default="",
        help="write the batch report JSON to this path for notifications",
    )
    parser.add_argument(
        "--max-minutes",
        type=int,
        default=0,
        help="stop cleanly after this many minutes (0=unlimited); "
        "partial progress is still published so a cancelled runner loses "
        "at most the in-flight issue",
    )
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

    def _needs_processing(issue: HistoricalIssue) -> bool:
        entry = state.get("issues", {}).get(issue.issue_id, {})
        if entry.get("status") != "complete":
            return True
        # A complete marker with no archive usually means a concurrent
        # publisher overwrote the public tree; re-archive from staging.
        archive = (
            PUBLIC_API
            / "journals"
            / journals[issue.journal]["id"]
            / "issues"
            / f"{issue.issue_id}.json"
        )
        return not archive.exists()

    pending = [issue for issue in plan if _needs_processing(issue)][: args.max_issues]
    reports: list[dict[str, Any]] = []
    remaining_translations = args.max_translations
    import time as _time

    started_at = _time.monotonic()
    budget_seconds = args.max_minutes * 60 if args.max_minutes > 0 else 0
    time_budget_reached = False
    summary_lines = [
        "| Issue | Result | Detail |",
        "|---|---|---|",
    ]
    for issue in pending:
        if args.translate and remaining_translations <= 0:
            break
        if budget_seconds and (_time.monotonic() - started_at) >= budget_seconds:
            time_budget_reached = True
            print(
                f"[backfill] time budget {args.max_minutes}m reached; "
                "publishing partial progress"
            )
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
        result = report.get("result", "")
        detail = report.get("error") or report.get("issue_id", "")
        print(f"[backfill] {issue.issue_id}: {result} {detail}")
        summary_lines.append(
            f"| {issue.issue_id} | {result} | {str(detail).replace('|', '/')} |"
        )
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("### Field history backfill batch\n")
            handle.write("\n".join(summary_lines) + "\n")
    for key in selected:
        write_archive_index(
            journals[key]["id"],
            journals[key]["name"],
            updated_at=now_iso(),
        )
    update_indexes(journals, load_available_issues(journals, {}))
    final_report = {
        "results": reports,
        "remaining_translation_budget": remaining_translations,
        "time_budget_reached": time_budget_reached,
    }
    if args.report_json:
        report_path = Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(final_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(final_report, ensure_ascii=False, indent=2))
    # Blocked issues (thin volumes, one-off translation failures) must not
    # discard the rest of the batch's progress. Exit 0 whenever anything was
    # processed; the notification email and status dashboard surface the
    # blocked issues. Exit 1 only when nothing could be processed at all.
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
