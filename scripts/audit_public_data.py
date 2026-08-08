from __future__ import annotations

import copy
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.article_types import (
    abstract_is_complete,
    normalize_issue_taxonomy,
    translation_is_complete,
)
from scripts.provenance_ledger import audit_claims, load_ledger
from scripts.translate_issue import TranslationError, validate_translation
from scripts.update_journals import (
    PUBLIC_API,
    article_type_overrides,
    validate_issue,
)


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def main(strict_provenance: bool = False) -> int:
    config = yaml.safe_load(
        (ROOT / "config" / "journals.yml").read_text(encoding="utf-8")
    )
    enabled = [
        journal
        for journal in config["journals"].values()
        if journal.get("enabled")
    ]
    findings: list[str] = []
    provenance_claims: dict[str, dict[str, Any]] = {}
    totals = {"journals": 0, "articles": 0, "translated": 0}

    collection_config = yaml.safe_load(
        (ROOT / "config" / "collections.yml").read_text(encoding="utf-8")
    )["collections"]
    configured_by_key = config["journals"]
    collected_ids: set[str] = set()
    collection_memberships: list[str] = []
    for collection_id, definition in collection_config.items():
        collection = read_json(PUBLIC_API / "collections" / f"{collection_id}.json")
        collection_ids = {
            journal["journal_id"] for journal in collection.get("journals", [])
        }
        expected_ids = {
            configured_by_key[key]["id"]
            for key in definition.get("journals", [])
            if key in configured_by_key and configured_by_key[key].get("enabled")
        }
        if collection_ids != expected_ids:
            findings.append(
                f"{collection_id} collection journal ids differ: "
                f"expected {sorted(expected_ids)}, got {sorted(collection_ids)}"
            )
        for journal_entry in collection.get("journals", []):
            if journal_entry.get("latest_issue_url") and journal_entry.get(
                "order_verification"
            ) not in {"official_verified", "pending_official"}:
                findings.append(
                    f"{collection_id}:{journal_entry.get('journal_id')}: "
                    "reader-facing order verification is missing"
                )
        collected_ids.update(collection_ids)
        collection_memberships.extend(collection_ids)
    all_expected_ids = {journal["id"] for journal in enabled}
    if collected_ids != all_expected_ids:
        findings.append(
            "enabled journals are not covered exactly once by public collections: "
            f"expected {sorted(all_expected_ids)}, got {sorted(collected_ids)}"
        )
    duplicate_memberships = sorted(
        journal_id
        for journal_id, count in Counter(collection_memberships).items()
        if count != 1
    )
    if duplicate_memberships:
        findings.append(
            "enabled journals must appear in exactly one public collection: "
            + ", ".join(duplicate_memberships)
        )

    monitoring_path = PUBLIC_API / "monitoring.json"
    if not monitoring_path.exists():
        findings.append("monitoring API is missing")
    else:
        monitoring = read_json(monitoring_path)
        if monitoring.get("schema_version") != "1.0":
            findings.append("monitoring API schema version is invalid")
        if monitoring.get("status") not in {"healthy", "degraded"}:
            findings.append("monitoring API status is invalid")
        if monitoring.get("summary", {}).get("configured_journals") != len(enabled):
            findings.append("monitoring API configured journal count is invalid")
        known_keys = set(config["journals"])
        surfaced = set(monitoring.get("warning_journals", [])) | set(
            monitoring.get("failed_journals", [])
        )
        if not surfaced <= known_keys:
            findings.append("monitoring API contains an unknown journal code")

    health_path = PUBLIC_API / "health.json"
    if not health_path.exists():
        findings.append("health API is missing")
    else:
        health = read_json(health_path)
        health_summary = health.get("summary", {})
        if health.get("schema_version") != "1.0":
            findings.append("health API schema version is invalid")
        if health_summary.get("enabled_journals") != len(enabled):
            findings.append("health API enabled journal count is invalid")
        if health_summary.get("available_journals") != len(enabled):
            findings.append("health API available journal count is invalid")

    manifest_path = ROOT / "public" / "project-manifest.json"
    if not manifest_path.exists():
        findings.append("project manifest is missing")
    else:
        manifest = read_json(manifest_path)
        if manifest.get("journal_count") != len(enabled):
            findings.append("project manifest journal count is invalid")
        field_count = len(enabled) - len(
            collection_config.get("top5", {}).get("journals", [])
        )
        if manifest.get("field_journal_count") != field_count:
            findings.append("project manifest field journal count is invalid")

    for journal in enabled:
        path = (
            PUBLIC_API
            / "journals"
            / journal["id"]
            / "issues"
            / "current.json"
        )
        if not path.exists():
            findings.append(f"{journal['id']}: current issue JSON is missing")
            continue
        issue = read_json(path)
        try:
            validate_issue(issue)
        except ValueError as error:
            findings.append(f"{journal['id']}: {error}")
            continue
        normalized = normalize_issue_taxonomy(
            copy.deepcopy(issue),
            overrides=article_type_overrides(),
        )
        if normalized != issue:
            findings.append(
                f"{journal['id']}: snapshot taxonomy is not normalized"
            )
        quality = issue.get("quality", {})
        if isinstance(quality.get("browser_order_verification"), dict):
            provenance_claims[journal["id"]] = {
                **quality,
                "issue_id": issue.get("issue_id", ""),
            }
        articles = issue["articles"]
        issue_year_match = re.search(
            r"\b(20\d{2})\b", str(issue.get("publication_date", ""))
        )
        article_years = [
            int(match.group(1))
            for article in articles
            if (
                match := re.search(
                    r"\b(20\d{2})\b",
                    str(article.get("publication_date", "")),
                )
            )
        ]
        if issue_year_match and article_years:
            issue_year = int(issue_year_match.group(1))
            newest_article_year = max(article_years)
            if issue_year < newest_article_year:
                findings.append(
                    f"{journal['id']}: issue publication year {issue_year} "
                    f"predates article metadata year {newest_article_year}"
                )
        totals["journals"] += 1
        totals["articles"] += len(articles)
        if issue["research_article_count"] != len(articles):
            findings.append(f"{journal['id']}: article count mismatch")
        counts = issue.get("content_counts") or issue.get("quality", {}).get(
            "content_counts", {}
        )
        if counts:
            excluded = issue.get("quality", {}).get("excluded_items", [])
            if int(counts.get("publishable_items", -1)) != len(articles):
                findings.append(f"{journal['id']}: publishable count mismatch")
            if int(counts.get("observed_items", -1)) != len(articles) + len(excluded):
                findings.append(f"{journal['id']}: observed item count mismatch")
            if int(counts.get("official_items", 0)) < int(counts.get("observed_items", 0)):
                findings.append(f"{journal['id']}: official item count is below observed items")
        if [article["sequence"] for article in articles] != list(
            range(1, len(articles) + 1)
        ):
            findings.append(f"{journal['id']}: sequence is not contiguous")
        dois = [article["doi"] for article in articles]
        if not all(dois) or len(dois) != len(set(dois)):
            findings.append(f"{journal['id']}: DOI completeness/uniqueness failed")
        for article in articles:
            label = f"{journal['id']}:{article['doi'] or article['paper_id']}"
            if not article["authors"]:
                findings.append(f"{label}: authors missing")
            if not abstract_is_complete(article):
                findings.append(f"{label}: English abstract missing")
            if not translation_is_complete(article):
                findings.append(f"{label}: Chinese content missing or lacks source abstract")
                continue
            if not article["abstract_en"]:
                totals["translated"] += 1
                continue
            try:
                validate_translation(
                    article,
                    {
                        "title_cn": article["title_cn"],
                        "abstract_cn": article["abstract_cn"],
                    },
                )
            except TranslationError as error:
                findings.append(f"{label}: {error}")
            else:
                totals["translated"] += 1

    if totals["journals"] != len(enabled):
        findings.append(
            f"available journals {totals['journals']}/{len(enabled)}"
        )
    if health_path.exists():
        health_summary = read_json(health_path).get("summary", {})
        if health_summary.get("articles") != totals["articles"]:
            findings.append("health API article count differs from current snapshots")
        if health_summary.get("translated_articles") != totals["translated"]:
            findings.append(
                "health API translation count differs from current snapshots"
            )

    # 人工/浏览器确认的官网顺序不像采集器那样每轮自证，因此单独对账：
    # 公开数据声称"已核对"的，台账里必须有对应且未过期的凭据。
    provenance_findings = audit_claims(provenance_claims, load_ledger())
    if provenance_findings:
        if strict_provenance:
            findings.extend(provenance_findings)
        else:
            for finding in provenance_findings:
                print(f"WARN provenance {finding}")
    if findings:
        for finding in findings:
            print(f"FAIL {finding}")
        return 1
    print(
        "public data audit: "
        f"{totals['journals']} journals, "
        f"{totals['articles']} articles, "
        f"{totals['translated']} translations verified"
    )
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict-provenance",
        action="store_true",
        help=(
            "Treat missing or expired browser-confirmation evidence as a "
            "failure instead of a warning. Enable once the ledger is populated."
        ),
    )
    raise SystemExit(main(parser.parse_args().strict_provenance))
