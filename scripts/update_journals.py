from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.aea import fetch_current_issue as fetch_aea
from scripts.translate_issue import translate_missing


PUBLIC_API = ROOT / "public" / "api" / "v1"
SCHEMA_PATH = ROOT / "schemas" / "issue.schema.json"
JOURNALS_PATH = ROOT / "config" / "journals.yml"
TRANSLATION_CACHE = ROOT / "data" / "translation-cache"
COMMENT_TITLE_OVERRIDES = {
    "10.1086/740225": "国家起源：土地生产率还是可攫取性？——评论",
}
UPDATE_REPORT = ROOT / "output" / "journal-update-report.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def validate_issue(issue: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(issue), key=lambda error: list(error.path))
    if errors:
        messages = [
            f"{'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors
        ]
        raise ValueError("Issue schema validation failed:\n" + "\n".join(messages))


def apply_translation_cache(issue: dict[str, Any]) -> dict[str, Any]:
    cache_path = TRANSLATION_CACHE / f"{issue['journal_id']}.json"
    cache = read_json(cache_path) or {}
    for article in issue["articles"]:
        translated = cache.get(article.get("doi", ""), {})
        if article.get("article_type") == "comment" and not article.get("title_cn"):
            override = COMMENT_TITLE_OVERRIDES.get(article.get("doi", ""))
            if override:
                article["title_cn"] = override
                article["quality_flags"] = [
                    flag for flag in article["quality_flags"] if flag != "title_cn_missing"
                ]
        if translated.get("title_cn"):
            article["title_cn"] = translated["title_cn"]
            article["quality_flags"] = [
                flag for flag in article["quality_flags"] if flag != "title_cn_missing"
            ]
        if translated.get("abstract_cn"):
            article["abstract_cn"] = translated["abstract_cn"]
            article["quality_flags"] = [
                flag
                for flag in article["quality_flags"]
                if flag != "abstract_cn_missing"
            ]
        provenance = translated.get("translation", {})
        if provenance:
            article["translation"].update(provenance)
        if article["title_cn"] and article["abstract_cn"]:
            article["translation"]["status"] = "complete"
        elif article["title_cn"] or article["abstract_cn"]:
            article["translation"]["status"] = "partial"

    translation_complete = sum(
        bool(article["title_cn"])
        and (bool(article["abstract_cn"]) or article.get("article_type") == "comment")
        for article in issue["articles"]
    )
    issue["quality"]["translation_complete"] = translation_complete
    flags = [
        flag for flag in issue["quality"]["flags"] if flag != "translation_incomplete"
    ]
    if translation_complete != issue["research_article_count"]:
        flags.append("translation_incomplete")
    issue["quality"]["flags"] = flags
    issue["status"] = "ready" if not flags else "incomplete"
    return issue


def collector_for(config: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    collector = config["collector"]
    current_url = config["current_issue_url"]
    if config.get("rss_url"):
        from collectors.metadata_fallback import fetch_official_rss_issue

        return lambda: fetch_official_rss_issue(
            journal_id=config["id"],
            journal_name=config["name"],
            issn=str(config["issn"]),
            current_issue_url=current_url,
            rss_url=config["rss_url"],
            repec_jpe=config.get("fallback") == "crossref-repec",
        )
    if collector == "aea":
        return lambda: fetch_aea(current_url)
    if collector == "chicago":
        from collectors.chicago import fetch_current_issue

        return lambda: fetch_current_issue(current_url)
    if collector == "oup":
        from collectors.oup import fetch_current_issue

        return lambda: fetch_current_issue(config["id"], current_url)
    if collector == "wiley":
        from collectors.wiley import fetch_current_issue

        return lambda: fetch_current_issue(current_url)
    if collector == "elsevier":
        from collectors.elsevier import fetch_current_issue

        return lambda: fetch_current_issue(
            journal_id=config["id"],
            journal_name=config["name"],
            issn=str(config["issn"]),
            repec_series_url=config["repec_series_url"],
            issue_url_template=config["issue_url_template"],
        )
    raise ValueError(f"Unknown collector: {collector}")


def fallback_collector_for(config: dict[str, Any]) -> Callable[[], dict[str, Any]] | None:
    fallback = config.get("fallback", "")
    if not fallback:
        return None
    if fallback == "repec":
        from collectors.elsevier import fetch_current_issue

        return lambda: fetch_current_issue(
            journal_id=config["id"],
            journal_name=config["name"],
            issn=str(config["issn"]),
            repec_series_url=config["repec_series_url"],
            issue_url_template=config["issue_url_template"],
        )
    from collectors.metadata_fallback import fetch_crossref_current_issue

    return lambda: fetch_crossref_current_issue(
        journal_id=config["id"],
        journal_name=config["name"],
        issn=str(config["issn"]),
        current_issue_url=config["current_issue_url"],
        repec_jpe=fallback == "crossref-repec",
    )


def public_issue_path(journal_id: str) -> Path:
    return PUBLIC_API / "journals" / journal_id / "issues" / "current.json"


def structural_flags(issue: dict[str, Any]) -> list[str]:
    content_only = {"translation_incomplete"}
    return [
        flag for flag in issue.get("quality", {}).get("flags", [])
        if flag not in content_only
    ]


def is_publishable_snapshot(issue: dict[str, Any]) -> bool:
    article_count = len(issue.get("articles", []))
    research_count = issue.get("research_article_count", 0)
    quality = issue.get("quality", {})
    required_abstract_count = sum(
        article.get("article_type") != "comment"
        for article in issue.get("articles", [])
    )
    return (
        research_count > 0
        and article_count == research_count
        and issue.get("expected_article_count") == research_count
        and bool(quality.get("roster_match"))
        and bool(quality.get("order_preserved"))
        and quality.get("doi_complete") == research_count
        and quality.get("authors_complete") == research_count
        and quality.get("abstract_en_complete", 0) >= required_abstract_count
        and quality.get("duplicate_count") == 0
        and not any(
            flag.startswith("collector_error:")
            for flag in quality.get("flags", [])
        )
    )


def collect_one(
    key: str,
    config: dict[str, Any],
    *,
    translate: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    report: dict[str, Any] = {
        "journal": key,
        "journal_id": config["id"],
        "source_url": config["current_issue_url"],
        "started_at": now_iso(),
    }
    target = public_issue_path(config["id"])
    previous = read_json(target)
    try:
        primary_error = ""
        try:
            issue = collector_for(config)()
            if not is_publishable_snapshot(issue):
                raise ValueError(
                    "primary collector failed publication gate: "
                    + ", ".join(structural_flags(issue) or ["empty official roster"])
                )
        except Exception as error:
            primary_error = f"{type(error).__name__}: {error}"
            fallback = fallback_collector_for(config)
            if fallback is None:
                raise
            issue = fallback()
            report["primary_error"] = primary_error
            report["transport"] = "metadata_fallback"
        issue = apply_translation_cache(issue)
        translation_report: dict[str, Any] | None = None
        if translate:
            if os.environ.get("GITHUB_TOKEN"):
                translation_report = translate_missing(
                    issue,
                    TRANSLATION_CACHE / f"{config['id']}.json",
                )
                issue = apply_translation_cache(issue)
            elif issue["quality"]["translation_complete"] < issue["research_article_count"]:
                translation_report = {
                    "journal_id": config["id"],
                    "translated": 0,
                    "failed": [],
                    "skipped": "GITHUB_TOKEN unavailable",
                }
        validate_issue(issue)
        if not is_publishable_snapshot(issue):
            raise ValueError(
                "collector result failed the publication gate: "
                + ", ".join(structural_flags(issue) or ["empty official roster"])
            )
        write_json(target, issue)
        readback = read_json(target)
        if readback is None or readback.get("issue_id") != issue["issue_id"]:
            raise RuntimeError("public issue write-back verification failed")
        report.update(
            {
                "result": "updated",
                "issue_id": issue["issue_id"],
                "articles": issue["research_article_count"],
                "content_status": (
                    "complete"
                    if issue["quality"]["translation_complete"]
                    == issue["research_article_count"]
                    else "translation_incomplete"
                ),
                "data_status": (
                    "healthy" if not structural_flags(issue) else "fallback"
                ),
                "translation": translation_report,
                "finished_at": now_iso(),
            }
        )
        return issue, report
    except Exception as error:
        report.update(
            {
                "result": "preserved_previous" if previous else "failed",
                "error": f"{type(error).__name__}: {error}",
                "finished_at": now_iso(),
            }
        )
        return previous, report


def load_available_issues(
    journal_configs: dict[str, dict[str, Any]],
    refreshed: dict[str, dict[str, Any] | None],
) -> dict[str, dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {}
    for key, config in journal_configs.items():
        if not config.get("enabled"):
            continue
        issue = refreshed.get(key)
        if issue is None:
            issue = read_json(public_issue_path(config["id"]))
        if issue:
            try:
                validate_issue(issue)
            except ValueError:
                continue
            available[key] = issue
    return available


def update_indexes(
    journal_configs: dict[str, dict[str, Any]],
    issues: dict[str, dict[str, Any]],
) -> None:
    updated_at = now_iso()
    collection_config = yaml.safe_load(
        (ROOT / "config" / "collections.yml").read_text(encoding="utf-8")
    )["collections"]
    journal_entries: dict[str, dict[str, Any]] = {}
    checks: dict[str, Any] = {}
    enabled_count = 0
    usable_count = 0
    translated_articles = 0
    total_articles = 0

    for key, config in journal_configs.items():
        if not config.get("enabled"):
            continue
        enabled_count += 1
        issue = issues.get(key)
        entry: dict[str, Any] = {
            "journal_id": config["id"],
            "short_name": config["short_name"],
            "name": config["name"],
            "name_cn": config.get("name_cn", ""),
            "field": config.get("field", "general"),
            "tier": config.get("tier", "S"),
            "collections": config.get("collections", []),
            "status": "unavailable",
        }
        if issue and is_publishable_snapshot(issue):
            usable_count += 1
            translated = issue["quality"]["translation_complete"]
            total = issue["research_article_count"]
            translated_articles += translated
            total_articles += total
            entry.update(
                {
                    "status": issue["status"],
                    "data_status": (
                        "healthy" if not structural_flags(issue) else "needs_attention"
                    ),
                    "content_status": (
                        "complete" if translated == total else "translation_incomplete"
                    ),
                    "latest_issue_id": issue["issue_id"],
                    "latest_issue_url": (
                        f"/journals/api/v1/journals/{config['id']}"
                        "/issues/current.json"
                    ),
                    "article_count": total,
                    "translation_complete": translated,
                }
            )
            checks[f"{config['id']}_roster_match"] = issue["quality"]["roster_match"]
            checks[f"{config['id']}_order_preserved"] = issue["quality"]["order_preserved"]
            checks[f"{config['id']}_primary_transport"] = not bool(
                structural_flags(issue)
            )
        else:
            checks[f"{config['id']}_available"] = False
        journal_entries[key] = entry

    data_healthy = usable_count == enabled_count and all(
        value is True for value in checks.values()
    )
    content_complete = total_articles > 0 and translated_articles == total_articles
    collection_indexes: list[dict[str, Any]] = []
    for collection_id, definition in collection_config.items():
        keys = [
            key
            for key in definition.get("journals", [])
            if key in journal_entries
        ]
        entries = [journal_entries[key] for key in keys]
        collection_usable = sum(
            bool(entry.get("latest_issue_url")) for entry in entries
        )
        collection_translated = sum(
            int(entry.get("translation_complete", 0)) for entry in entries
        )
        collection_articles = sum(int(entry.get("article_count", 0)) for entry in entries)
        collection_data_healthy = bool(entries) and collection_usable == len(entries) and all(
            entry.get("data_status") == "healthy" for entry in entries
        )
        collection_content_complete = (
            collection_articles > 0 and collection_translated == collection_articles
        )
        payload = {
            "schema_version": "1.0",
            "collection_id": collection_id,
            "title": definition["name"],
            "title_cn": definition.get("name_cn", ""),
            "updated_at": updated_at,
            "data_status": "healthy" if collection_data_healthy else "degraded",
            "content_status": "complete" if collection_content_complete else "translation_incomplete",
            "summary": {
                "configured_journals": len(entries),
                "available_journals": collection_usable,
                "articles": collection_articles,
                "translated_articles": collection_translated,
            },
            "journals": entries,
        }
        write_json(PUBLIC_API / "collections" / f"{collection_id}.json", payload)
        collection_indexes.append(
            {
                "id": collection_id,
                "title": definition["name"],
                "title_cn": definition.get("name_cn", ""),
                "url": f"/journals/api/v1/collections/{collection_id}.json",
            }
        )
    health = {
        "schema_version": "1.0",
        "updated_at": updated_at,
        "status": "healthy" if data_healthy else "degraded",
        "content_status": "complete" if content_complete else "translation_incomplete",
        "summary": {
            "enabled_journals": enabled_count,
            "available_journals": usable_count,
            "articles": total_articles,
            "translated_articles": translated_articles,
        },
        "checks": checks,
    }
    index = {
        "schema_version": "1.0",
        "updated_at": updated_at,
        "collections": collection_indexes,
    }
    manifest = {
        "project_id": "journals",
        "title": "Academic Door Journals",
        "description": "经济学期刊目录、双语摘要与 Academic Door Composer",
        "url": "https://academic-door.github.io/journals/",
        "updated_at": updated_at,
        "status": health["status"],
        "latest_title": f"期刊最新卷期 · {usable_count}/{enabled_count} 家期刊可用",
        "latest_url": "https://academic-door.github.io/journals/",
        "data_url": "https://academic-door.github.io/journals/api/v1/index.json",
        "feed_url": "",
    }
    write_json(PUBLIC_API / "health.json", health)
    write_json(PUBLIC_API / "index.json", index)
    write_json(ROOT / "public" / "project-manifest.json", manifest)

    for collection_id, definition in collection_config.items():
        readback = read_json(PUBLIC_API / "collections" / f"{collection_id}.json")
        expected = sum(key in journal_entries for key in definition.get("journals", []))
        if readback is None or len(readback.get("journals", [])) != expected:
            raise RuntimeError(f"{collection_id} collection write-back verification failed")


def main() -> int:
    config = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))
    journal_configs = config["journals"]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--journal",
        default="ALL",
        choices=["ALL", *journal_configs.keys()],
    )
    parser.add_argument(
        "--translate",
        action="store_true",
        help="Translate missing Chinese titles and abstracts through GitHub Models.",
    )
    args = parser.parse_args()

    selected = [
        key
        for key, journal in journal_configs.items()
        if journal.get("enabled") and (args.journal == "ALL" or key == args.journal)
    ]
    refreshed: dict[str, dict[str, Any] | None] = {}
    reports: list[dict[str, Any]] = []
    for key in selected:
        issue, report = collect_one(
            key,
            journal_configs[key],
            translate=args.translate,
        )
        refreshed[key] = issue
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False))

    available = load_available_issues(journal_configs, refreshed)
    update_indexes(journal_configs, available)
    final_report = {
        "updated_at": now_iso(),
        "requested": args.journal,
        "translate": args.translate,
        "results": reports,
        "available_journals": sorted(available),
    }
    write_json(UPDATE_REPORT, final_report)
    return 1 if any(item["result"] == "failed" for item in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
