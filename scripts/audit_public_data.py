from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.translate_issue import TranslationError, validate_translation
from scripts.update_journals import PUBLIC_API, validate_issue


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def main() -> int:
    config = yaml.safe_load(
        (ROOT / "config" / "journals.yml").read_text(encoding="utf-8")
    )
    enabled = [
        journal
        for journal in config["journals"].values()
        if journal.get("enabled")
    ]
    findings: list[str] = []
    totals = {"journals": 0, "articles": 0, "translated": 0}

    collection = read_json(PUBLIC_API / "collections" / "top5.json")
    collection_ids = {
        journal["journal_id"] for journal in collection.get("journals", [])
    }
    expected_ids = {journal["id"] for journal in enabled}
    if collection_ids != expected_ids:
        findings.append(
            "collection journal ids differ: "
            f"expected {sorted(expected_ids)}, got {sorted(collection_ids)}"
        )

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
        articles = issue["articles"]
        totals["journals"] += 1
        totals["articles"] += len(articles)
        if issue["research_article_count"] != len(articles):
            findings.append(f"{journal['id']}: article count mismatch")
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
            if not article["abstract_en"]:
                findings.append(f"{label}: English abstract missing")
            if not article["title_cn"] or not article["abstract_cn"]:
                findings.append(f"{label}: Chinese content missing")
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
    raise SystemExit(main())
