"""JOEG 26(5): exact official OUP roster -> nine bilingual staged records.

Read publisher-deposited Crossref abstracts for the first eight DOIs and the
public CC BY OUP article-minimal page for the ninth. The source roster is the
first-party OUP issue fixture; Crossref never substitutes as roster authority.
Does not edit #491/#492 Executor-owned branches, data or public production.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.jae_crossref_audit import _plain_abstract, _normalized
from scripts.build_archives_from_roster_evidence import build_candidate_from_evidence
from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    apply_translation_cache,
    is_archivable_snapshot,
    issue_translation_semantics_valid,
    normalize_issue_content,
    validate_issue,
)

FIXTURE = ROOT / "tests/fixtures/b8/joeg-26-5-official-toc.json"
OFFICIAL = "https://academic.oup.com/joeg/issue/26/5"
OUP_OA_URL = "https://oup.silverchair-cdn.com/article-minimal/8525017"
OA_DOI = "10.1093/jeg/lbag009"
OA_TITLE = (
    "What are transfer mechanisms of regional entrepreneurship over time? "
    "The impact of historical role models and their economic success"
)
EXPECTED_DOIS = (
    "10.1093/jeg/lbag036", "10.1093/jeg/lbag026",
    "10.1093/jeg/lbag047", "10.1093/jeg/lbag041",
    "10.1093/jeg/lbag002", "10.1093/jeg/lbag006",
    "10.1093/jeg/lbag008", "10.1093/jeg/lbag012",
    OA_DOI, "10.1093/jeg/lbag061",
)
SECTIONS = ("anniversary_commentary",) * 3 + ("emerging_voices",) + ("original_article",) * 5 + ("correction",)
USER_AGENT = "AcademicDoorJournals/0.1 (scholarly metadata; https://academic-door.github.io/)"


def official_rows(stage: dict[str, Any]) -> list[dict[str, Any]]:
    rows = stage.get("source_items", [])
    if (
        stage.get("journal") != "JOEG"
        or stage.get("issue_id") != "joeg-26-5"
        or stage.get("verified_full_displayed_toc") is not True
        or stage.get("source_item_count") != 10
        or stage.get("enabled") is not False
        or tuple(x.get("doi") for x in rows) != EXPECTED_DOIS
        or tuple(x.get("publisher_section") for x in rows) != SECTIONS
        or [x.get("source_sequence") for x in rows] != list(range(1, 11))
    ):
        raise ValueError("joeg_26_5_official_roster_mismatch")
    return rows


def _get(session: requests.Session, url: str, *, timeout: int) -> requests.Response:
    for attempt in range(4):
        response = session.get(
            url, timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        if response.status_code not in (429, 503) or attempt == 3:
            response.raise_for_status()
            return response
        raw = response.headers.get("Retry-After", "")
        delay = int(raw) if raw.isdigit() else 2 ** (attempt + 1)
        time.sleep(min(30, max(1, delay)))
    raise RuntimeError("unreachable")


def fetch_english(
    stage: dict[str, Any], session: requests.Session, *, timeout: int = 15,
) -> list[dict[str, Any]]:
    rows = official_rows(stage)
    found: list[dict[str, Any]] = []
    for row in rows[:8]:
        doi = row["doi"]
        item = _get(
            session, "https://api.crossref.org/works/" + doi, timeout=timeout
        ).json()["message"]
        if (
            str(item.get("DOI", "")).casefold() != doi
            or not (item.get("title") or [])
            or not item.get("author")
        ):
            raise ValueError(f"{doi}: Crossref DOI/author/title source mismatch")
        authors = [
            " ".join(filter(None, [x.get("given", ""), x.get("family", "")])).strip()
            for x in item["author"]
        ]
        abstract = _plain_abstract(str(item.get("abstract") or ""))
        if len(abstract) < 80 or not all(authors):
            raise ValueError(f"{doi}: incomplete deposited English source")
        found.append({
            "doi": doi, "title_en": item["title"][0],
            "authors": authors, "abstract_en": abstract,
            "abstract_source": "https://api.crossref.org/works/" + doi,
            "article_url": "https://doi.org/" + doi,
            "license_urls": [str(x.get("URL", "")) for x in item.get("license", [])],
        })
        time.sleep(0.25)

    # This is an ordinary public first-party OUP CDN endpoint for an OPEN
    # ACCESS CC BY article, not a workaround for the OUP publisher 403 path.
    html = _get(session, OUP_OA_URL, timeout=timeout).text
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1")
    section = soup.select_one("section.abstract")
    title_text = heading.get_text(" ", strip=True) if heading else ""
    if (
        not section
        or not title_text
        or _normalized(title_text) != _normalized(OA_TITLE)
        or OA_DOI not in soup.get_text(" ", strip=True)
        or "creativecommons.org/licenses/by/4.0" not in html
        or "Michael Wyrwich" not in html
        or "Michael Fritsch" not in html
    ):
        raise ValueError("joeg_oa_first_party_identity_or_license_mismatch")
    abstract = " ".join(section.get_text(" ", strip=True).split())
    if len(abstract) < 100:
        raise ValueError("joeg_oa_first_party_abstract_missing")
    found.append({
        "doi": OA_DOI, "title_en": OA_TITLE,
        "authors": ["Michael Wyrwich", "Michael Fritsch"],
        "abstract_en": abstract,
        "abstract_source": OUP_OA_URL,
        "article_url": "https://academic.oup.com/joeg/advance-article/doi/10.1093/jeg/lbag009/8525017",
        "license_urls": ["https://creativecommons.org/licenses/by/4.0/"],
    })
    return found


def make_candidate(
    stage: dict[str, Any], entries: list[dict[str, Any]], *, captured_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = official_rows(stage)
    if len(entries) != 9:
        raise ValueError("joeg_english_batch_not_nine")
    for row, item in zip(rows[:9], entries, strict=True):
        if row["doi"] != item.get("doi"):
            raise ValueError("joeg_source_doi_or_order_mismatch")
        if (
            not item.get("title_en") or not item.get("authors")
            or len(str(item.get("abstract_en", ""))) < 80
            or not item.get("abstract_source")
        ):
            raise ValueError("joeg_source_missing_en_payload")
    evidence = {
        "official_url": OFFICIAL,
        "journal_id": "joeg", "issue_id": "joeg-26-5",
        "captured_at": captured_at,
        "method": "official-page-read",
        "items": [
            {
                "doi": row["doi"],
                "title_en": item["title_en"],
                "authors": item["authors"],
                "abstract_en": item["abstract_en"],
                "source_url": OFFICIAL,
            }
            for row, item in zip(rows[:9], entries, strict=True)
        ],
        "excluded_items": [
            {
                "doi": EXPECTED_DOIS[9],
                "title_en": "Correction to: Medium-run local economic effects of a major earthquake",
                "reason": "publisher_correction_excluded",
            }
        ],
    }
    candidate = build_candidate_from_evidence(
        evidence, {"name": "Journal of Economic Geography", "issn": "1468-2702"},
        {}, publication_date="September 2026",
    )
    if len(candidate["articles"]) != 9:
        raise ValueError("joeg_qualified_nine_papers_not_retained")
    for i, (article, item) in enumerate(zip(candidate["articles"], entries, strict=True)):
        article["article_type"] = "comment" if i < 3 else "research-article"
        article["sources"]["issue"] = OFFICIAL
        article["sources"]["roster"] = OFFICIAL
        article["sources"]["metadata"] = item["abstract_source"] if i == 8 else "https://api.crossref.org/works/" + item["doi"]
        article["sources"]["abstract_en"] = item["abstract_source"]
        article["source_url"] = item["article_url"]
    candidate["quality"]["roster_authority"] = "official-issue-page"
    candidate["quality"]["roster_transport"] = "publisher-full-toc-observed"
    candidate = normalize_issue_content(candidate)
    validate_issue(candidate)
    if candidate["source_status"] != "official_verified":
        raise ValueError("joeg_publisher_official_source_drift")
    # Comment + research mix must follow actual OUP issue sections; extra
    # COMMENT rows do not redefine the original 6 research-type papers.
    if [a["article_type"] for a in candidate["articles"]] != (
        ["comment"] * 3 + ["research-article"] * 6
    ):
        raise ValueError("joeg_official_comment_research_type_mismatch")
    report = {
        "journal": "JOEG", "issue_id": "joeg-26-5",
        "official_roster_dois": list(EXPECTED_DOIS),
        "article_count": 9, "excluded_correction": EXPECTED_DOIS[9],
        "crossref_abstracts": 8, "first_party_open_access_abstracts": 1,
        "open_access_oa_doi": OA_DOI,
        "license_urls_by_doi": {e["doi"]: e["license_urls"] for e in entries},
        "source_status": candidate["source_status"],
        "production_writes": False, "release_enabled": False,
    }
    return candidate, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=FIXTURE)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--translate", action="store_true")
    args = parser.parse_args()
    stage = json.loads(args.stage.read_text(encoding="utf-8"))
    session = requests.Session()
    source = fetch_english(stage, session)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    candidate, report = make_candidate(stage, source, captured_at=now)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    translated = None
    if args.translate:
        cache = args.output_dir / "joeg-26-5.translation-cache.json"
        translated = translate_missing(candidate, cache, max_translations=9)
        candidate = apply_translation_cache(candidate, cache_path=cache)
        candidate = normalize_issue_content(candidate)
        validate_issue(candidate)
    report.update({
        "content_status": candidate["content_status"],
        "publication_state": candidate["publication_state"],
        "quality_ready": (
            candidate["publication_state"] == "ready"
            and is_archivable_snapshot(candidate)
            and issue_translation_semantics_valid(candidate)
        ),
        "translation": translated,
    })
    for name, payload in [
        ("joeg-26-5.candidate.json", candidate),
        ("joeg-26-5.report.json", report),
    ]:
        (args.output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({
        k: report[k] for k in (
            "issue_id", "article_count", "crossref_abstracts",
            "first_party_open_access_abstracts", "source_status",
            "content_status", "publication_state", "quality_ready",
            "translation", "release_enabled", "production_writes",
        )
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
