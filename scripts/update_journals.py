from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.aea import fetch_current_issue


PUBLIC_API = ROOT / "public" / "api" / "v1"
SCHEMA_PATH = ROOT / "schemas" / "issue.schema.json"
JOURNALS_PATH = ROOT / "config" / "journals.yml"
TRANSLATION_CACHE = ROOT / "data" / "translation-cache"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def validate_issue(issue: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(issue), key=lambda error: list(error.path))
    if errors:
        messages = [
            f"{'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors
        ]
        raise ValueError("Issue schema validation failed:\n" + "\n".join(messages))


def apply_translation_cache(issue: dict) -> dict:
    cache_path = TRANSLATION_CACHE / f"{issue['journal_id']}.json"
    if not cache_path.exists():
        return issue
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    for article in issue["articles"]:
        translated = cache.get(article["doi"], {})
        if translated.get("title_cn"):
            article["title_cn"] = translated["title_cn"]
            article["quality_flags"] = [
                flag for flag in article["quality_flags"] if flag != "title_cn_missing"
            ]
        if translated.get("abstract_cn"):
            article["abstract_cn"] = translated["abstract_cn"]
            article["quality_flags"] = [
                flag for flag in article["quality_flags"] if flag != "abstract_cn_missing"
            ]
        if article["title_cn"] and article["abstract_cn"]:
            article["translation"]["status"] = "complete"
        elif article["title_cn"] or article["abstract_cn"]:
            article["translation"]["status"] = "partial"

    translation_complete = sum(
        bool(article["title_cn"] and article["abstract_cn"])
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


def update_aer(config: dict) -> dict:
    issue = fetch_current_issue(config["current_issue_url"])
    issue = apply_translation_cache(issue)
    validate_issue(issue)
    write_json(
        PUBLIC_API / "journals" / "aer" / "issues" / "current.json",
        issue,
    )
    return issue


def update_indexes(issue: dict) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    issue_url = (
        "/journals/api/v1/journals/aer/issues/current.json"
    )
    top5 = {
        "schema_version": "1.0",
        "collection_id": "top5",
        "title": "Top 5 Economics Journals",
        "updated_at": now,
        "journals": [
            {
                "journal_id": "aer",
                "short_name": "AER",
                "name": issue["journal_name"],
                "status": issue["status"],
                "latest_issue_id": issue["issue_id"],
                "latest_issue_url": issue_url,
                "article_count": issue["research_article_count"],
            },
            {"journal_id": "jpe", "short_name": "JPE", "status": "detected"},
            {"journal_id": "qje", "short_name": "QJE", "status": "detected"},
            {"journal_id": "res", "short_name": "RES", "status": "detected"},
            {"journal_id": "ecta", "short_name": "ECTA", "status": "detected"},
        ],
    }
    health = {
        "schema_version": "1.0",
        "updated_at": now,
        "status": "healthy" if issue["status"] == "ready" else "degraded",
        "checks": {
            "aer_roster_match": issue["quality"]["roster_match"],
            "aer_order_preserved": issue["quality"]["order_preserved"],
            "aer_translation_complete": (
                issue["quality"]["translation_complete"]
                == issue["research_article_count"]
            ),
        },
    }
    index = {
        "schema_version": "1.0",
        "updated_at": now,
        "collections": [
            {
                "id": "top5",
                "title": "Top 5 Economics Journals",
                "url": "/journals/api/v1/collections/top5.json",
            }
        ],
    }
    manifest = {
        "project_id": "journals",
        "title": "Academic Door Journals",
        "description": "经济学期刊监测、中文目录与 Academic Door Composer",
        "url": "https://academic-door.github.io/journals/",
        "updated_at": now,
        "status": health["status"],
        "latest_title": f"AER Vol. {issue['volume']}, No. {issue['issue']}",
        "latest_url": "https://academic-door.github.io/journals/top5/",
        "data_url": "https://academic-door.github.io/journals/api/v1/index.json",
        "feed_url": "",
    }
    write_json(PUBLIC_API / "collections" / "top5.json", top5)
    write_json(PUBLIC_API / "health.json", health)
    write_json(PUBLIC_API / "index.json", index)
    write_json(ROOT / "public" / "project-manifest.json", manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", default="AER", choices=["AER"])
    args = parser.parse_args()

    config = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))
    journal_config = config["journals"][args.journal]
    issue = update_aer(journal_config)
    update_indexes(issue)
    print(
        json.dumps(
            {
                "journal": args.journal,
                "issue_id": issue["issue_id"],
                "articles": issue["research_article_count"],
                "status": issue["status"],
                "quality_flags": issue["quality"]["flags"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
