"""Build an article-level manifest for the translation-only recovery queue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.translate_issue import _source_hash, validate_translation


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_comment_without_abstract(article: dict[str, Any]) -> bool:
    return article.get("article_type") == "comment" and not article.get("abstract_en")


def _translation_probe(article: dict[str, Any], cache: dict[str, Any]) -> tuple[str, str]:
    doi = str(article.get("doi", "")).strip().casefold()
    entry = cache.get(doi)
    if not isinstance(entry, dict):
        return "missing", "no cache entry"
    if not str(entry.get("title_cn", "")).strip():
        return "missing", "title_cn missing"
    if not str(entry.get("abstract_cn", "")).strip() and not _is_comment_without_abstract(article):
        return "missing", "abstract_cn missing"
    try:
        validate_translation(article, entry)
    except Exception as error:
        return "invalid", str(error)
    source_hash = _source_hash(article)
    cached_hash = str(entry.get("source_hash", "")).strip()
    if cached_hash and cached_hash != source_hash:
        return "invalid", "source_hash mismatch"
    return "valid", ""


def _target_files(issue_root: Path, issue_id: str, journal_id: str) -> list[Path]:
    return [
        issue_root / "issues" / f"{issue_id}.json",
        issue_root / "data" / "backfill-staging" / journal_id / f"{issue_id}.json",
        issue_root / "public" / "api" / "v1" / "journals" / journal_id / "issues" / f"{issue_id}.json",
    ]


def build_manifest(issue_root: Path, cache_root: Path, issue_ids: list[str]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    missing_issue_files: list[str] = []
    for issue_id in issue_ids:
        journal_id = issue_id.split("-", 1)[0].casefold()
        issue_path = next((path for path in _target_files(issue_root, issue_id, journal_id) if path.exists()), None)
        if issue_path is None:
            missing_issue_files.append(issue_id)
            issues.append({
                "issue_id": issue_id,
                "article_count": 0,
                "valid_cached_translations": 0,
                "missing_translations": [],
                "invalid_translations": [],
                "expected_model_calls": 0,
                "blocker": "issue archive/candidate file not available locally",
            })
            continue
        issue = _read(issue_path)
        cache_path = cache_root / f"{str(issue.get('journal_id', journal_id)).casefold()}.json"
        cache = _read(cache_path) if cache_path.exists() else {}
        valid: list[dict[str, str]] = []
        missing: list[dict[str, str]] = []
        invalid: list[dict[str, str]] = []
        for article in issue.get("articles", []):
            if not isinstance(article, dict):
                continue
            doi = str(article.get("doi", "")).strip().casefold()
            title = str(article.get("title_en", "")).strip()
            if not doi:
                continue
            kind, reason = _translation_probe(article, cache)
            item = {"doi": doi, "title_en": title, "source_hash": _source_hash(article)}
            if reason:
                item["reason"] = reason
            {"valid": valid, "missing": missing, "invalid": invalid}[kind].append(item)
        issues.append({
            "issue_id": issue_id,
            "journal_id": str(issue.get("journal_id", journal_id)),
            "source_file": str(issue_path),
            "publication_state": issue.get("publication_state", issue.get("status", "")),
            "source_status": issue.get("source_status", ""),
            "content_status": issue.get("content_status", ""),
            "article_count": len(issue.get("articles", [])),
            "valid_cached_translations": len(valid),
            "missing_translations": missing,
            "invalid_translations": invalid,
            "expected_model_calls": len(missing) + len(invalid),
        })
    totals = {
        "issues": len(issues),
        "article_count": sum(item["article_count"] for item in issues),
        "valid_cached_translations": sum(item["valid_cached_translations"] for item in issues),
        "missing_translations": sum(len(item["missing_translations"]) for item in issues),
        "invalid_translations": sum(len(item["invalid_translations"]) for item in issues),
        "expected_model_calls": sum(item["expected_model_calls"] for item in issues),
    }
    return {
        "schema_version": "1.0",
        "issue_ids": issue_ids,
        "missing_issue_files": missing_issue_files,
        "totals": totals,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--issue-ids", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    issue_ids = sorted({value.strip() for value in args.issue_ids.split(",") if value.strip()})
    payload = build_manifest(args.issue_root, args.cache_root, issue_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["totals"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
