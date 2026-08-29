"""Translate only an explicit issue subset and stage successful issue archives."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.translate_issue import translate_missing
from scripts.build_translation_fix_manifest import _translation_probe
from scripts.update_journals import (
    apply_translation_cache,
    archive_issue,
    is_archivable_snapshot,
    normalize_issue_content,
    stamp_issue_readiness,
    validate_issue,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _issue_path(api_root: Path, staging_root: Path, issue_id: str) -> Path | None:
    journal = issue_id.split("-", 1)[0].casefold()
    staging = staging_root / journal / f"{issue_id}.json"
    public = api_root / "journals" / journal / "issues" / f"{issue_id}.json"
    return staging if staging.exists() else public if public.exists() else None


def run_subset(
    api_root: Path,
    staging_root: Path,
    cache_root: Path,
    issue_ids: list[str],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for issue_id in sorted(set(issue_ids)):
        path = _issue_path(api_root, staging_root, issue_id)
        if path is None:
            results.append({"issue_id": issue_id, "result": "issue-file-missing", "failed": []})
            continue
        issue = _read(path)
        journal = str(issue.get("journal_id", issue_id.split("-", 1)[0])).casefold()
        cache_path = cache_root / f"{journal}.json"
        cache = _read(cache_path) if cache_path.exists() else {}
        valid_cached_before = sum(
            1
            for article in issue.get("articles", [])
            if isinstance(article, dict) and _translation_probe(article, cache)[0] == "valid"
        )
        before = int((issue.get("quality") or {}).get("translation_complete") or 0)
        report: dict[str, Any] = {}
        try:
            report = translate_missing(issue, cache_path, max_translations=None)
            issue = apply_translation_cache(issue, cache_path=cache_path)
            issue = normalize_issue_content(issue)
            stamp_issue_readiness(issue)
            validate_issue(issue)
            failed = []
            for item in report.get("failed", []):
                failed.append({
                    **item,
                    "issue_id": issue_id,
                    "source_hash": item.get("source_hash", ""),
                    "provider": item.get("provider", ""),
                    "model": item.get("model", report.get("model", "")),
                })
            staging = staging_root / journal / f"{issue_id}.json"
            staging.parent.mkdir(parents=True, exist_ok=True)
            staging.write_text(json.dumps(issue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            archived = None
            if is_archivable_snapshot(issue):
                archived = archive_issue(issue, api_root=api_root, replace_non_ready=True)
            after = int((issue.get("quality") or {}).get("translation_complete") or 0)
            results.append({
                "issue_id": issue_id,
                "result": "ready" if archived is not None else issue.get("publication_state", "blocked"),
                "publication_state": issue.get("publication_state", ""),
                "source_status": issue.get("source_status", ""),
                "content_status": issue.get("content_status", ""),
                "validated_before": before,
                "validated_after": after,
                "translation_model_calls": int(report.get("translated", 0) or 0),
                "translation_cache_reuses": valid_cached_before,
                "failed": failed,
                "staging": str(staging),
            })
        except Exception as error:
            failed = []
            for item in report.get("failed", []):
                failed.append({
                    **item,
                    "issue_id": issue_id,
                    "source_hash": item.get("source_hash", ""),
                    "provider": item.get("provider", ""),
                    "model": item.get("model", report.get("model", "")),
                })
            if not failed:
                failed = [{
                    "issue_id": issue_id,
                    "doi": "",
                    "title_en": "",
                    "source_hash": "",
                    "provider": "pipeline",
                    "model": "",
                    "error": str(error),
                }]
            results.append({"issue_id": issue_id, "result": "error", "failed": failed})
    return {
        "issue_ids": sorted(set(issue_ids)),
        "results": results,
        "translation_model_calls": sum(int(item.get("translation_model_calls", 0) or 0) for item in results),
        "translation_cache_reuses": sum(int(item.get("translation_cache_reuses", 0) or 0) for item in results),
        "publishable_issue_ids": sorted(item["issue_id"] for item in results if item.get("result") == "ready"),
        "failed_issue_ids": sorted(item["issue_id"] for item in results if item.get("failed")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--issue-ids", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = run_subset(
        args.api_root,
        args.staging_root,
        args.cache_root,
        [value.strip() for value in args.issue_ids.split(",") if value.strip()],
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
