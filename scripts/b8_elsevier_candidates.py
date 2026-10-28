"""Four Elsevier B8 journals: stage publisher-supplied RePEc issue candidates.

The RePEc serial pages identify a publisher-supplied DOI/PII ordered candidate,
NOT an independently verified first-party ScienceDirect issue TOC. Every result
remains source_status=publisher_verified and production_enabled=False.
No product config, public, data branch, or shared collector changes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.jae_crossref_audit import _normalized
from collectors.article_types import evidence_roster_article_type
from collectors.metadata_fallback import (
    _parse_repec_serial_issues,
    _repec_detail_metadata,
)
from scripts.build_archives_from_roster_evidence import build_candidate_from_evidence
from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    apply_translation_cache, is_archivable_snapshot,
    issue_translation_semantics_valid, normalize_issue_content,
    validate_issue,
)

# Immutable 2026-10-28 publisher-supplied serial page DOI/PII order;
# sha256 hashes the exact ordered /a/... paths, never article body.
SOURCE_SPECS = {
    "IJIO": {
        "series": "indorg", "volume": "108", "count": 6,
        "url_sha256": "b75fd91d3eee28dedcba368f9697f183244b8c51eec4ee13fad67c7fb74d1ebc",
        "name": "International Journal of Industrial Organization",
        "issn": "0167-7187",
    },
    "JFI": {
        "series": "jfinin", "volume": "67", "count": 7,
        "url_sha256": "2ba2464ec60e79f4aa9d080bc22c6858fb79a88c80e594b4167e473f7c8734e8",
        "name": "Journal of Financial Intermediation",
        "issn": "1042-9573",
    },
    "EJPE": {
        "series": "poleco", "volume": "94", "count": 17, "excluded_corrections": 1,
        "url_sha256": "a5027c56db7b633d70a3d6ee020a7253b1c5bfe6bc3f981f0ff05f08c607375c",
        "name": "European Journal of Political Economy",
        "issn": "0176-2680",
    },
    "JIMF": {
        "series": "jimfin", "volume": "168", "count": 6,
        "url_sha256": "d087b6680cf9de7fe2692cc6c545878c7f3f74d398f869b1e0f5d1ffa9cb77b9",
        "name": "Journal of International Money and Finance",
        "issn": "0261-5606",
    },
}
USER_AGENT = "AcademicDoorJournals/0.1 (research metadata; https://academic-door.github.io/)"


def _get(session: requests.Session, url: str, *, timeout: int) -> requests.Response:
    for attempt in range(4):
        r = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if r.status_code not in (429, 503) or attempt == 3:
            r.raise_for_status()
            return r
        h = r.headers.get("Retry-After", "")
        delay = int(h) if h.isdigit() else 2 ** (attempt + 1)
        time.sleep(min(30, max(1, delay)))
    raise RuntimeError("unreachable")


def discover_serial(
    key: str, session: requests.Session, *, timeout: int = 15
) -> tuple[str, list[dict[str, str]]]:
    if key not in SOURCE_SPECS:
        raise ValueError("unapproved B8 Elsevier title")
    spec = SOURCE_SPECS[key]
    url = f"https://ideas.repec.org/s/eee/{spec['series']}.html"
    page = _get(session, url, timeout=timeout)
    issues = _parse_repec_serial_issues(page.content, url)
    rows = next(
        (i["items"] for i in issues if
         i["year"] == "2026"
         and str(i["volume"]).casefold() == spec["volume"]
         and str(i["issue"]).casefold() == "c"),
        None,
    )
    if not rows or len(rows) != spec["count"]:
        raise ValueError(f"{key}: source issue missing or count drift")
    paths = ["/" + x["detail_url"].split("ideas.repec.org/", 1)[1] for x in rows]
    observed = hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()
    if observed != spec["url_sha256"]:
        raise ValueError(f"{key}: publisher-supplied PII/order drift")
    if len(set(paths)) != len(paths):
        raise ValueError(f"{key}: duplicate PII in source issue")
    return url, rows


def fetch_records(
    key: str, rows: list[dict[str, str]], session: requests.Session,
    *, timeout: int = 15,
) -> list[dict[str, str | list[str]]]:
    spec = SOURCE_SPECS[key]
    result = []
    for index, row in enumerate(rows, 1):
        doi, authors_repec, abstract = _repec_detail_metadata(
            session, row["detail_url"], timeout=timeout,
        )
        doi = doi.strip().casefold()
        correction = evidence_roster_article_type(row["title"]) == "correction"
        if not doi.startswith("10.1016/") or (not correction and len(abstract) < 80):
            raise ValueError(f"{key} row {index}: source DOI/abstract missing")
        item = _get(
            session, "https://api.crossref.org/works/" + doi,
            timeout=timeout,
        ).json()["message"]
        if (
            str(item.get("DOI", "")).casefold() != doi
            or str(item.get("volume")) != spec["volume"]
            or (not correction and not item.get("author"))
            or not item.get("title")
        ):
            raise ValueError(f"{key} row {index}: Crossref identity missing")
        if _normalized(html.unescape(item["title"][0])) != _normalized(html.unescape(row["title"])):
            raise ValueError(f"{key} row {index}: serial vs Crossref title mismatch")
        authors = [
            " ".join(filter(None, (a.get("given", ""), a.get("family", "")))).strip()
            for a in item.get("author", [])
        ]
        if not correction and not all(authors):
            raise ValueError(f"{key} row {index}: missing authors")
        # PII is supplied by the publisher's series-linked record; link to
        # ScienceDirect as a locator but never claim it was fetched.
        path = row["detail_url"].rsplit("/", 1)[-1]
        match = re.search(r"c(s[0-9a-z]+)\.html$", path, re.I)
        if not match:
            raise ValueError(f"{key} row {index}: missing PII path")
        pii = match.group(1).upper()
        result.append({
            "doi": doi, "title_en": row["title"],
            "authors": authors, "abstract_en": "" if correction else abstract,
            "abstract_source_url": row["detail_url"],
            "publisher_article_url":
                "https://www.sciencedirect.com/science/article/pii/" + pii,
            "crossref_url": "https://api.crossref.org/works/" + doi,
        })
        time.sleep(0.3)
    return result


def candidate_for(
    key: str, serial_url: str,
    entries: list[dict[str, str | list[str]]], *, captured_at: str,
) -> dict:
    spec = SOURCE_SPECS[key]
    if len(entries) != spec["count"]:
        raise ValueError(f"{key}: incomplete source DOI batch")
    dois = [e["doi"] for e in entries]
    if len(set(dois)) != len(dois):
        raise ValueError(f"{key}: duplicate DOI")
    evidence = {
        "official_url": serial_url,
        "journal_id": key.lower(),
        "issue_id": f"{key.lower()}-{spec['volume']}-c",
        "captured_at": captured_at, "method": "repec-publisher-supplied",
        "items": [
            {
                "doi": e["doi"], "title_en": e["title_en"],
                "authors": e["authors"],
                "abstract_en": e["abstract_en"],
                "source_url": e["publisher_article_url"],
            } for e in entries
        ],
        "excluded_items": [],
    }
    candidate = build_candidate_from_evidence(
        evidence, {"name": spec["name"], "issn": spec["issn"]}, {},
        publication_date="2026",
    )
    expected_excluded = spec.get("excluded_corrections", 0)
    actual_excluded = len(candidate["quality"].get("excluded_items", []))
    if (
        len(candidate["articles"]) != spec["count"] - expected_excluded
        or actual_excluded != expected_excluded
        or any(
            x.get("article_type") != "correction"
            for x in candidate["quality"].get("excluded_items", [])
        )
    ):
        raise ValueError(f"{key}: article type exclusions need owner review")
    for article, item in zip(candidate["articles"], entries, strict=True):
        article["sources"]["issue"] = serial_url
        article["sources"]["roster"] = serial_url
        article["sources"]["metadata"] = item["crossref_url"]
        article["sources"]["abstract_en"] = item["abstract_source_url"]
        article["source_url"] = item["publisher_article_url"]
    candidate["quality"]["roster_authority"] = "repec-publisher-supplied"
    candidate["quality"]["roster_transport"] = "repec-serial-page"
    candidate["quality"]["first_party_official_issue_toc_verified"] = False
    candidate = normalize_issue_content(candidate)
    validate_issue(candidate)
    if candidate["source_status"] != "publisher_verified":
        raise ValueError(f"{key}: source falsely promoted to first-party")
    return candidate


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--journal", choices=sorted(SOURCE_SPECS), required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--translate", action="store_true")
    args = p.parse_args()
    sess = requests.Session()
    url, rows = discover_serial(args.journal, sess)
    data = fetch_records(args.journal, rows, sess)
    candidate = candidate_for(
        args.journal, url, data,
        captured_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    translations = None
    if args.translate:
        cache = args.output_dir / f"{args.journal.lower()}.translation-cache.json"
        translations = translate_missing(candidate, cache, max_translations=len(data))
        candidate = apply_translation_cache(candidate, cache_path=cache)
        candidate = normalize_issue_content(candidate)
        validate_issue(candidate)
    report = {
        "journal": args.journal, "issue_id": candidate["issue_id"],
        "publisher_series_count": len(data),
        "publishable_articles": len(candidate["articles"]),
        "excluded_corrections": len(candidate["quality"].get("excluded_items", [])),
        "source_status": candidate["source_status"],
        "first_party_full_toc_verified": False,
        "content_status": candidate["content_status"],
        "publication_state": candidate["publication_state"],
        "content_quality_ready": (
            is_archivable_snapshot(candidate)
            and issue_translation_semantics_valid(candidate)
        ),
        "translation": translations, "release_enabled": False,
        "production_writes": False,
    }
    for name, obj in (
        ("candidate.json", candidate), ("report.json", report),
    ):
        (args.output_dir / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
