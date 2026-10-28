"""ITPF 33(5): stage actual 11 publisher-supplied RePEc abstracts and translations.

The Springer-publisher-supplied RePEc *serial* inventory is a qualified
secondary source for exact issue membership, not a first-party Springer TOC.
Crossref enriches author metadata only. No direct producer, data branch,
public content, rights-clearance or title activation mutation occurs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.jae_crossref_audit import _normalized
from collectors.metadata_fallback import _repec_detail_metadata
from scripts.build_archives_from_roster_evidence import build_candidate_from_evidence
from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    apply_translation_cache,
    is_archivable_snapshot,
    issue_translation_semantics_valid,
    normalize_issue_content,
    validate_issue,
)

STAGE = ROOT / "tests/fixtures/b8/springer-itpf-33-5-publisher-series-stage.json"
USER_AGENT = "AcademicDoorJournals/0.1 (academic metadata; https://academic-door.github.io/)"


def assert_source_stage(stage: dict[str, Any]) -> list[dict[str, Any]]:
    rows = stage.get("observed_articles", [])
    if (
        stage.get("issue_id") != "itpf-33-5"
        or stage.get("publisher_supplied_serial_url")
        != "https://ideas.repec.org/s/kap/itaxpf.html"
        or stage.get("verified_publisher_supplied_11_item_sequence") is not True
        or stage.get("first_party_springer_full_toc_verified") is not False
        or stage.get("enabled") is not False
        or len(rows) != 11
        or [r.get("source_sequence") for r in rows] != list(range(1, 12))
        or len({r.get("doi") for r in rows}) != 11
    ):
        raise ValueError("itpf_unqualified_publisher_series")
    if not all(
        str(row["doi"]).startswith("10.1007/s10797-")
        and row["repec_record_url"].startswith(
            "https://ideas.repec.org/a/kap/itaxpf/v33y2026i5d"
        )
        for row in rows
    ):
        raise ValueError("itpf_series_foreign_doi_or_article")
    return rows


def _get_crossref(session: requests.Session, doi: str, timeout: int) -> dict[str, Any]:
    for attempt in range(4):
        resp = session.get(
            f"https://api.crossref.org/works/{doi}",
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if resp.status_code not in (429, 503) or attempt == 3:
            resp.raise_for_status()
            return resp.json()["message"]
        hdr = resp.headers.get("Retry-After", "")
        delay = int(hdr) if hdr.isdigit() else 2 ** (attempt + 1)
        time.sleep(min(30, max(1, delay)))
    raise RuntimeError("unreachable")


def fetch_source_fields(
    stage: dict[str, Any],
    session: requests.Session,
    *,
    timeout: int = 15,
) -> list[dict[str, Any]]:
    """Fetch exact RePEc abstract record plus DOI-verified Crossref authors."""
    rows = assert_source_stage(stage)
    result = []
    for row in rows:
        doi = row["doi"]
        # RePEc is publisher-supplied; verify DOI appears on the *detail* page.
        found_doi, _repec_authors, abstract = _repec_detail_metadata(
            session, row["repec_record_url"], timeout=timeout,
        )
        if found_doi.casefold() != doi or len(abstract) < 80:
            raise ValueError(f"{doi}: publisher-supplied article abstract/identity missing")
        entry = _get_crossref(session, doi, timeout)
        if (
            str(entry.get("DOI", "")).casefold() != doi
            or str(entry.get("volume")) != "33"
            or str(entry.get("issue")) != "5"
            or not entry.get("author")
        ):
            raise ValueError(f"{doi}: Crossref article identity/authors mismatch")
        deposited_title = (entry.get("title") or [""])[0]
        if _normalized(deposited_title) != _normalized(row["title_en"]):
            raise ValueError(f"{doi}: publisher series title differs from Crossref")
        authors = [
            " ".join(filter(None, [x.get("given", ""), x.get("family", "")])).strip()
            for x in entry["author"]
        ]
        if not all(authors):
            raise ValueError(f"{doi}: empty author name")
        result.append({
            "doi": doi,
            "title_en": row["title_en"],
            "authors": authors,
            "abstract": abstract,
            "abstract_source_url": row["repec_record_url"],
        })
        time.sleep(0.3)
    return result


def make_candidate(
    stage: dict[str, Any],
    fetched: list[dict[str, Any]],
    *,
    captured_at: str,
) -> dict[str, Any]:
    rows = assert_source_stage(stage)
    if len(fetched) != 11:
        raise ValueError("itpf_missing_source_records")
    for row, item in zip(rows, fetched, strict=True):
        if item["doi"] != row["doi"] or len(item.get("abstract", "")) < 80:
            raise ValueError("itpf_source_order_or_abstract_mismatch")
        if _normalized(item.get("title_en", "")) != _normalized(row["title_en"]):
            raise ValueError("itpf_title_mismatch")
        if not item.get("authors") or item.get("abstract_source_url") != row["repec_record_url"]:
            raise ValueError("itpf_source_provenance_mismatch")
    serial_url = stage["publisher_supplied_serial_url"]
    # Adapter reuses the existing issue builder, immediately relabels its
    # provisional source fact as *publisher-supplied RePEc*, not first-party.
    evidence = {
        "official_url": serial_url,
        "journal_id": "itpf",
        "issue_id": "itpf-33-5",
        "captured_at": captured_at,
        "method": "repec-publisher-supplied",
        "items": [
            {
                "doi": row["doi"],
                "title_en": row["title_en"],
                "authors": record["authors"],
                "abstract_en": record["abstract"],
                "source_url": row["publisher_article_url"],
            }
            for row, record in zip(rows, fetched, strict=True)
        ],
        "excluded_items": [],
    }
    candidate = build_candidate_from_evidence(
        evidence,
        {"name": "International Tax and Public Finance", "issn": "0927-5940"},
        {},
        publication_date="October 2026",
    )
    for article, row in zip(candidate["articles"], rows, strict=True):
        article["sources"]["roster"] = serial_url
        article["sources"]["issue"] = serial_url
        article["sources"]["abstract_en"] = row["repec_record_url"]
        article["sources"]["metadata"] = f"https://api.crossref.org/works/{row['doi']}"
        article["source_url"] = row["publisher_article_url"]
    candidate["quality"]["roster_authority"] = "repec-publisher-supplied"
    candidate["quality"]["roster_transport"] = "repec-serial-page"
    candidate["quality"]["first_party_official_issue_toc_verified"] = False
    candidate["source_url"] = serial_url
    candidate = normalize_issue_content(candidate)
    validate_issue(candidate)
    if candidate["source_status"] != "publisher_verified":
        raise ValueError("itpf source was incorrectly promoted to official_verified")
    if len(candidate["articles"]) != 11:
        raise ValueError("itpf normalized membership incomplete")
    return candidate


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", type=Path, default=STAGE)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--translate", action="store_true")
    p.add_argument("--timeout", type=int, default=15)
    args = p.parse_args()
    stage = json.loads(args.stage.read_text(encoding="utf-8"))
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    records = fetch_source_fields(stage, session, timeout=args.timeout)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    candidate = make_candidate(stage, records, captured_at=now)
    translated = None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.translate:
        cache = args.output_dir / "itpf-33-5.translation-cache.json"
        translated = translate_missing(candidate, cache, max_translations=11)
        candidate = apply_translation_cache(candidate, cache_path=cache)
        candidate = normalize_issue_content(candidate)
        validate_issue(candidate)
    result = {
        "issue_id": "itpf-33-5",
        "publisher_supplied_articles": len(candidate["articles"]),
        "first_party_full_toc_verified": False,
        "source_status": candidate["source_status"],
        "content_status": candidate["content_status"],
        "publication_state": candidate["publication_state"],
        "content_quality_ready": (
            candidate["publication_state"] == "ready"
            and is_archivable_snapshot(candidate)
            and issue_translation_semantics_valid(candidate)
        ),
        "translation": translated,
        "release_enabled": False,
        "production_writes": False,
    }
    for name, payload in (
        ("itpf-33-5.candidate.json", candidate),
        ("itpf-33-5.report.json", result),
    ):
        (args.output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
