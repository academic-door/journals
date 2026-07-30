from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "journals.yml"
PUBLIC_ROOT = ROOT / "public" / "api" / "v1" / "journals"
DEFAULT_OUTPUT = ROOT / "public" / "api" / "v1" / "source-audit.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def source_status(issue: dict[str, Any], errors: list[str]) -> str:
    if errors or not issue.get("quality", {}).get("roster_match"):
        return "needs_attention"
    quality = issue.get("quality", {})
    flags = set(quality.get("flags", []))
    authority = str(quality.get("roster_authority", ""))
    if authority == "publisher-rss":
        return "publisher_feed_verified"
    if {
        "official_order_unverified",
        "crossref_provisional_roster",
        "publisher_html_blocked_crossref_fallback",
        "publisher_html_blocked_repec_fallback",
    } & flags:
        return "fallback_verified"
    return "official_page_verified"


def audit_snapshot(
    issue: dict[str, Any] | None,
    *,
    configured_source: str,
) -> dict[str, Any]:
    if issue is None:
        return {
            "available": False,
            "status": "missing",
            "errors": ["snapshot_missing"],
            "warnings": [],
        }
    quality = issue.get("quality", {})
    articles = issue.get("articles", [])
    excluded = quality.get("excluded_items", [])
    counts = issue.get("content_counts") or quality.get("content_counts") or {}
    errors: list[str] = []
    warnings: list[str] = []
    if int(issue.get("research_article_count", -1)) != len(articles):
        errors.append("article_count_mismatch")
    if int(quality.get("excluded_item_count", len(excluded))) != len(excluded):
        errors.append("excluded_count_mismatch")
    if counts:
        if int(counts.get("publishable_items", -1)) != len(articles):
            errors.append("publishable_count_mismatch")
        if int(counts.get("observed_items", -1)) != len(articles) + len(excluded):
            errors.append("observed_count_mismatch")
        if int(counts.get("official_items", 0)) < int(counts.get("observed_items", 0)):
            errors.append("official_count_below_observed")
    else:
        warnings.append("content_counts_missing")
    source_host = urlparse(str(issue.get("source_url", ""))).hostname or ""
    configured_host = urlparse(configured_source).hostname or ""
    if not source_host:
        errors.append("source_url_missing")
    elif configured_host and source_host != configured_host:
        warnings.append("snapshot_uses_alternate_official_source")
    flags = [str(flag) for flag in quality.get("flags", [])]
    for flag in (
        "official_order_unverified",
        "crossref_provisional_roster",
        "abstract_en_incomplete",
        "translation_incomplete",
    ):
        if flag in flags:
            warnings.append(flag)
    status = source_status(issue, errors)
    return {
        "available": True,
        "status": status,
        "issue_id": issue.get("issue_id", ""),
        "publication_date": issue.get("publication_date", ""),
        "publication_state": issue.get("publication_state", "enriching"),
        "source_url": issue.get("source_url", ""),
        "roster_authority": quality.get("roster_authority", "official-issue-page"),
        "roster_transport": quality.get("roster_transport", "official-issue-page"),
        "order_verified": "official_order_unverified" not in flags
        and "crossref_provisional_roster" not in flags,
        "content_counts": counts,
        "abstracts_complete": int(quality.get("abstract_en_complete", 0)),
        "translations_complete": int(quality.get("translation_complete", 0)),
        "errors": errors,
        "warnings": list(dict.fromkeys(warnings)),
    }


def build_audit(
    *,
    config_path: Path = CONFIG_PATH,
    public_root: Path = PUBLIC_ROOT,
) -> dict[str, Any]:
    configs = yaml.safe_load(config_path.read_text(encoding="utf-8"))["journals"]
    journals: list[dict[str, Any]] = []
    for key, config in configs.items():
        if not config.get("enabled"):
            continue
        issue_root = public_root / str(config["id"]) / "issues"
        current = audit_snapshot(
            read_json(issue_root / "current.json"),
            configured_source=str(config["current_issue_url"]),
        )
        detected = audit_snapshot(
            read_json(issue_root / "detected.json"),
            configured_source=str(config["current_issue_url"]),
        )
        effective = detected if detected.get("available") else current
        journals.append(
            {
                "key": key,
                "journal_id": config["id"],
                "short_name": config["short_name"],
                "name": config["name"],
                "publisher": config.get("publisher", ""),
                "configured_source": config["current_issue_url"],
                "status": effective.get("status", "missing"),
                "current": current,
                "detected": detected,
            }
        )
    statuses = [item["status"] for item in journals]
    invariant_errors = sum(
        len(snapshot.get("errors", []))
        for item in journals
        for snapshot in (item["current"], item["detected"])
        if snapshot.get("available")
    )
    return {
        "schema_version": "1.0",
        "updated_at": now_iso(),
        "status": "healthy" if invariant_errors == 0 and "missing" not in statuses else "degraded",
        "summary": {
            "configured_journals": len(journals),
            "official_page_verified": statuses.count("official_page_verified"),
            "publisher_feed_verified": statuses.count("publisher_feed_verified"),
            "fallback_verified": statuses.count("fallback_verified"),
            "needs_attention": statuses.count("needs_attention"),
            "missing": statuses.count("missing"),
            "invariant_errors": invariant_errors,
        },
        "journals": journals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit journal source and issue-count alignment")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--public-root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    payload = build_audit(config_path=args.config, public_root=args.public_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = payload["summary"]
    print(
        "source alignment audit: "
        f"{summary['configured_journals']} journals, "
        f"{summary['invariant_errors']} invariant errors, "
        f"{summary['needs_attention']} need attention"
    )
    if args.strict and (
        summary["invariant_errors"]
        or summary["needs_attention"]
        or summary["missing"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
