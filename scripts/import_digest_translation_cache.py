from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.translate_issue import _source_hash, validate_translation


SECTION_PATTERN = re.compile(
    r"^####\s+\d+\.\s+(?P<title>.+?)\s*$"
    r"(?P<body>.*?)(?=^####\s+\d+\.|\Z)",
    re.MULTILINE | re.DOTALL,
)
FIELD_PATTERN = {
    "abstract_en": re.compile(
        r"【\*\*Abstract\*\*】\s*\n+(?P<value>.*?)(?=\n+【\*\*摘要\*\*】)",
        re.DOTALL,
    ),
    "abstract_cn": re.compile(
        r"【\*\*摘要\*\*】\s*\n+(?P<value>.*?)(?=\n+【\*\*DOI\*\*】)",
        re.DOTALL,
    ),
    "doi": re.compile(
        r"【\*\*DOI\*\*】\s*\n+(?:https?://doi\.org/)?(?P<value>10\.\d{4,9}/\S+)",
        re.DOTALL | re.IGNORECASE,
    ),
}


def clean(value: str) -> str:
    return " ".join(value.split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--issue", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()

    issue = json.loads(args.issue.read_text(encoding="utf-8"))
    articles = {article["doi"].lower(): article for article in issue["articles"]}
    cache = (
        json.loads(args.cache.read_text(encoding="utf-8"))
        if args.cache.exists()
        else {}
    )
    imported = 0
    skipped: list[str] = []
    text = args.source.read_text(encoding="utf-8")
    for section in SECTION_PATTERN.finditer(text):
        body = section.group("body")
        values: dict[str, str] = {}
        for field, pattern in FIELD_PATTERN.items():
            match = pattern.search(body)
            values[field] = clean(match.group("value")) if match else ""
        doi = values["doi"].rstrip(".,);").lower()
        article = articles.get(doi)
        if article is None:
            skipped.append(doi or section.group("title"))
            continue
        translated = {
            "title_cn": next(
                (
                    candidate["title_cn"]
                    for candidate in issue["articles"]
                    if candidate["doi"].lower() == doi and candidate.get("title_cn")
                ),
                "",
            ),
            "abstract_cn": values["abstract_cn"],
        }
        if not translated["title_cn"]:
            # The directory section has the Chinese title immediately below the English title.
            toc_match = re.search(
                rf"\*\*{re.escape(article['title_en'])}\*\*\s*\n(?P<title_cn>[^\n]+)",
                text,
            )
            translated["title_cn"] = clean(toc_match.group("title_cn")) if toc_match else ""
        try:
            validate_translation(article, translated)
        except Exception as error:
            skipped.append(f"{doi}: {error}")
            continue
        cache[doi] = {
            "title_cn": translated["title_cn"],
            "abstract_cn": translated["abstract_cn"],
            "translation": {
                "provider": "legacy-reviewed-digest",
                "model": "",
                "prompt_version": "academic-door-abstract-zh-v1",
            },
            "source_hash": _source_hash(article),
        }
        imported += 1

    args.cache.parent.mkdir(parents=True, exist_ok=True)
    args.cache.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"imported": imported, "skipped": skipped}, ensure_ascii=False))
    return 0 if imported else 1


if __name__ == "__main__":
    raise SystemExit(main())
