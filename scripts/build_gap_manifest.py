"""Build the issue-level historical recovery queue.

The manifest is deliberately derived from discovery snapshots and archive
read-back.  A state entry alone can never make an issue recoverable or ready.
The output is safe to publish as an operational report: it contains issue
metadata and gate results, not publisher HTML, credentials, or raw abstracts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_status import (
    DEFAULT_JOURNALS_CONFIG,
    discovery_expectations,
    load_journals,
    load_state,
)
from scripts.backfill_history import inspect_archive


TARGET_YEARS = {2023, 2024, 2025, 2026}
ACTIONABLE_CATEGORIES = {
    "recoverable",
    "browser_required",
    "translation_required",
    "source_pending",
    "blocked",
}


def _normalise_year(value: Any) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if year in TARGET_YEARS else None


def _is_crossref_candidate(authority: str, entry: dict[str, Any]) -> bool:
    text = " ".join(
        str(value)
        for value in (
            authority,
            entry.get("last_error", ""),
            entry.get("source_status", ""),
        )
    ).casefold()
    return "crossref" in text or "provisional" in text


def _requires_browser(reason: str, authority: str, entry: dict[str, Any]) -> bool:
    text = " ".join(
        str(value)
        for value in (
            reason,
            authority,
            entry.get("last_error", ""),
            entry.get("retry_class", ""),
        )
    ).casefold()
    return any(
        marker in text
        for marker in (
            "browser",
            "official",
            "publisher",
            "science direct",
            "sciencedirect",
            "missing_abstract",
            "abstract_missing",
            "possible_incomplete_volume",
            "in progress",
            "awaiting_official",
        )
    )


def classify_gap(
    *,
    archive: dict[str, Any],
    entry: dict[str, Any],
    authority: str,
    collector: str = "",
    excluded: bool = False,
) -> tuple[str, str]:
    """Return ``(category, reason)`` using only persisted gate evidence."""

    if excluded:
        return "excluded_with_official_evidence", "officially excluded issue"

    publication_state = str(archive.get("publication_state", "blocked"))
    content_status = str(archive.get("content_status", "blocked"))
    source_status = str(archive.get("source_status", "source_pending"))
    reason = str(archive.get("reason") or entry.get("last_error") or "")

    if publication_state == "ready":
        return "ready", "content and official source gates passed"
    if content_status == "translation_partial" or str(
        entry.get("status", "")
    ) == "translation_partial":
        return "translation_required", reason or "translation incomplete"
    if archive.get("archive_exists") and content_status == "complete":
        if source_status in {"official_verified", "publisher_verified"}:
            return "blocked", reason or "archive read-back disagrees with state"
        return "source_pending", reason or "official source verification pending"
    if _requires_browser(reason, authority, entry):
        return "browser_required", reason or "official roster or abstract requires browser"
    if not archive.get("archive_exists"):
        if collector in {"elsevier", "aea", "chicago", "oup", "wiley", "repec"}:
            return "recoverable", reason or "official collector can retry this issue"
        if _is_crossref_candidate(authority, entry):
            return "browser_required", reason or "official roster requires browser evidence"
        return "recoverable", reason or "discovered issue has no archive"
    return "blocked", reason or "archive failed content gate"


def _archive_path(api_root: Path, journals: dict[str, Any], journal: str, issue_id: str) -> Path:
    config = journals.get(journal) or journals.get(journal.upper()) or {}
    return (
        api_root
        / "journals"
        / str(config.get("id", journal.casefold()))
        / "issues"
        / f"{issue_id}.json"
    )


def _counts(archive: dict[str, Any]) -> dict[str, int]:
    issue = archive.get("issue") or {}
    articles = issue.get("articles") or []
    quality = issue.get("quality") or {}
    return {
        "articles": int(issue.get("research_article_count") or len(articles)),
        "abstract_en": int(quality.get("abstract_en_complete") or 0),
        "translation_cn": int(quality.get("translation_complete") or 0),
    }


def build_manifest(
    *,
    state_paths: Iterable[Path],
    journals: dict[str, Any],
    api_root: Path,
) -> dict[str, Any]:
    states = [load_state(path) for path in state_paths]
    merged_issues: dict[str, Any] = {}
    for state in states:
        issues = state.get("issues") if isinstance(state.get("issues"), dict) else {}
        merged_issues.update(issues)

    expected = discovery_expectations(states, merged_issues)
    records: list[dict[str, Any]] = []
    discovered_journals: set[str] = set()
    excluded_ids: set[str] = set()
    for state in states:
        for journal, snapshot in (state.get("discovery") or {}).items():
            if not isinstance(snapshot, dict):
                continue
            discovered_journals.add(str(journal))
            for key in ("excluded_issue_ids", "excluded"):
                values = snapshot.get(key) or []
                if isinstance(values, list):
                    excluded_ids.update(str(value) for value in values)

    for issue_id, expectation in sorted(expected.items()):
        year = _normalise_year(expectation.get("year"))
        if year is None:
            continue
        journal = str(expectation.get("journal", ""))
        entry = dict(merged_issues.get(issue_id) or {})
        archive = inspect_archive(
            _archive_path(api_root, journals, journal, issue_id),
            expected_issue_id=issue_id,
            expected_journal_id=str((journals.get(journal) or {}).get("id", "")),
        )
        category, reason = classify_gap(
            archive=archive,
            entry=entry,
            authority=str(expectation.get("authority", "")),
            collector=str((journals.get(journal) or {}).get("collector", "")),
            excluded=issue_id in excluded_ids,
        )
        records.append(
            {
                "issue_id": issue_id,
                "journal": journal,
                "year": year,
                "volume": entry.get("volume") or expectation.get("volume", ""),
                "issue": entry.get("issue") or expectation.get("issue", ""),
                "official_url": entry.get("official_url")
                or expectation.get("official_url", ""),
                "authority": expectation.get("authority", ""),
                "refreshed_at": expectation.get("refreshed_at", ""),
                "collector_revision": expectation.get("collector_revision", ""),
                "category": category,
                "reason": reason,
                "status": entry.get("status", ""),
                "retry_class": entry.get("retry_class", ""),
                "archive_exists": bool(archive.get("archive_exists")),
                "content_status": archive.get("content_status", "blocked"),
                "source_status": archive.get("source_status", "source_pending"),
                "publication_state": archive.get("publication_state", "blocked"),
                "counts": _counts(archive),
                "next_action": {
                    "ready": "no action",
                    "recoverable": "run official collector and validate archive",
                    "browser_required": "read exact official issue page in authorized browser",
                    "translation_required": "translate missing articles using cache-aware queue",
                    "source_pending": "attach official roster/source evidence; do not publish",
                    "blocked": "inspect issue error and route to the correct retry queue",
                    "excluded_with_official_evidence": "retain exclusion evidence; do not collect",
                }[category],
            }
        )

    missing_discovery = sorted(
        set(journals) - discovered_journals
    )
    for journal in missing_discovery:
        records.append(
            {
                "issue_id": "",
                "journal": journal,
                "year": None,
                "volume": "",
                "issue": "",
                "official_url": "",
                "authority": "",
                "refreshed_at": "",
                "collector_revision": "",
                "category": "blocked",
                "reason": "discovery_snapshot_missing",
                "status": "",
                "retry_class": "manual",
                "archive_exists": False,
                "content_status": "blocked",
                "source_status": "source_pending",
                "publication_state": "blocked",
                "counts": {"articles": 0, "abstract_en": 0, "translation_cn": 0},
                "next_action": "refresh official discovery snapshot",
            }
        )

    counts = Counter(record["category"] for record in records)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "target_years": sorted(TARGET_YEARS),
        "journal_count": len(journals),
        "discovery_journals": sorted(discovered_journals),
        "discovery_snapshot_missing_journals": missing_discovery,
        "summary": dict(sorted(counts.items())),
        "records": records,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Academic Door historical gap manifest",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Summary",
        "",
    ]
    for category, count in payload["summary"].items():
        lines.append(f"- {category}: {count}")
    lines.extend(
        [
            "",
            "## Issue queue",
            "",
            "| Journal | Issue | Year | Category | Content | Source | Reason |",
            "|---|---|---:|---|---|---|---|",
        ]
    )
    for record in payload["records"]:
        reason = re.sub(r"\s+", " ", str(record.get("reason", ""))).replace("|", "/")
        lines.append(
            f"| {record['journal']} | {record['issue_id'] or '-'} | "
            f"{record.get('year') or '-'} | {record['category']} | "
            f"{record['content_status']} | {record['source_status']} | {reason[:120]} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", action="append", required=True)
    parser.add_argument("--api-root", default="public/api/v1")
    parser.add_argument("--journals-config", default=str(DEFAULT_JOURNALS_CONFIG))
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default="")
    args = parser.parse_args()
    payload = build_manifest(
        state_paths=[Path(value) for value in args.state],
        journals=load_journals(Path(args.journals_config)),
        api_root=Path(args.api_root),
    )
    output = Path(args.out_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_md:
        markdown = Path(args.out_md)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
