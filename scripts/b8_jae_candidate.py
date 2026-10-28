"""Build a real, isolated JAE 41(6) bilingual candidate from accepted sources.

Official Wiley TOC fixture is the authority for membership, types and order.
Publisher-deposited Crossref records may supplement article abstracts, never
override the official roster. Writes ONLY to caller-specified staging paths.
No production/data branch writes, journal enablement or license claims.
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.jae_crossref_audit import (
    CROSSREF_ENDPOINT,
    audit_issue,
    assert_official_roster,
    _plain_abstract,
)
from scripts.build_archives_from_roster_evidence import build_candidate_from_evidence
from scripts.import_official_roster_evidence import validate_evidence
from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    apply_translation_cache,
    is_archivable_snapshot,
    issue_translation_semantics_valid,
    normalize_issue_content,
    stamp_issue_readiness,
    validate_issue,
)

STAGE = ROOT / "tests/fixtures/b8/wiley-jae-41-6-source-stage.json"
USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)


def fetch_deposits(
    session: requests.Session, stage: dict[str, Any], *, timeout: int = 15
) -> list[dict[str, Any]]:
    rows = assert_official_roster(stage)
    deposits: list[dict[str, Any]] = []
    for row in rows:
        doi = row["doi"]
        # Respect Crossref public API rate limiting. A bounded Retry-After
        # delay is the ordinary supported API path, not an access bypass.
        for attempt in range(4):
            response = session.get(
                CROSSREF_ENDPOINT + doi, timeout=timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            if response.status_code not in (429, 503) or attempt == 3:
                response.raise_for_status()
                break
            retry_after = response.headers.get("Retry-After", "")
            delay = int(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1)
            time.sleep(min(30, max(1, delay)))
        item = response.json()["message"]
        time.sleep(0.25)  # Avoid bursting across the 12 accepted DOIs.
        if str(item.get("DOI", "")).casefold() != doi:
            raise ValueError(f"{doi}: unexpected Crossref DOI response")
        deposits.append(item)
    # Real source records must match the official 12 original DOI/title/author
    # identities and carry a nonempty abstract. No scraping of Wiley HTML.
    audit_issue(stage, deposits)
    return deposits



def restore_publisher_verified_math(doi: str, plain: str) -> tuple[str, bool]:
    """Repair an observed Crossref math omission using the first-party article.

    DOI 10.1002/jae.70077 has MathML formulas stripped in the Crossref
    deposit's plain text: chi-squared distribution, n^(-1/2) rate, and n.
    The publisher article abstract supplies these exact mathematical facts:
    https://onlinelibrary.wiley.com/doi/10.1002/jae.70077
    No change is made to any other DOI or to a future corrected deposit.
    """
    if doi != "10.1002/jae.70077":
        return plain, False
    if "parametric rate , where is the sample size." not in plain:
        return plain, False
    if "standard distribution under the null hypothesis" not in plain:
        raise ValueError("JAE 70077 math repair anchor unexpectedly changed")
    plain = plain.replace(
        "standard distribution under the null hypothesis",
        "standard chi-squared (χ²) distribution under the null hypothesis",
        1,
    )
    plain = plain.replace(
        "parametric rate , where is the sample size.",
        "parametric rate n^(-1/2), where n is the sample size.",
        1,
    )
    return plain, True


def make_candidate(
    stage: dict[str, Any], deposits: list[dict[str, Any]], *,
    retrieved_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = assert_official_roster(stage)
    evidence_report = audit_issue(stage, deposits)
    official_toc = stage["publisher_issue_index"]

    # This is a materialization of the previously accepted day-granularity
    # publisher observation, NOT a newly fetched Wiley page.
    original_day = str(stage["observed_on"])
    official_evidence = {
        "schema_version": "1.0",
        "capture_mode": "official-roster-evidence",
        "method": "official-page-read",
        "finalized": True,
        "captured_at": original_day + "T00:00:00+00:00",
        "capture_time_granularity": "day",
        "journal_id": "jae",
        "issue_id": "jae-41-6",
        "official_url": official_toc,
        "items": [],
        "excluded_items": [],
        "excluded_item_count": 0,
    }
    for row, deposit in zip(rows, deposits, strict=True):
        abstract, publisher_math_repaired = restore_publisher_verified_math(
            row["doi"], _plain_abstract(deposit["abstract"])
        )
        official_evidence["items"].append({
            "sequence": row["source_sequence"],
            "doi": row["doi"],
            "title_en": row["title_en"],
            "authors": list(row["authors_en"]),
            "abstract_en": abstract,
            "source_url": row["publisher_article_url"],
            "abstract_source_url": (
                row["publisher_article_url"] if publisher_math_repaired
                else CROSSREF_ENDPOINT + row["doi"]
            ),
        })
    validate_evidence(official_evidence)

    candidate = build_candidate_from_evidence(
        official_evidence,
        {"name": "Journal of Applied Econometrics", "issn": "0883-7252"},
        {},
        publication_date="September 2026",
    )
    if tuple(article["doi"] for article in candidate["articles"]) != tuple(
        row["doi"] for row in rows
    ):
        raise ValueError("JAE candidate lost official DOI order")
    if len(candidate["articles"]) != 12:
        raise ValueError("JAE official two Replication papers were not included")
    for article, row in zip(candidate["articles"], rows, strict=True):
        if article["article_type"] != "research-article":
            raise ValueError(f"{row['doi']}: original research/replication type drift")
        article["sources"]["issue"] = official_toc
        article["sources"]["roster"] = official_toc
        article["sources"]["metadata"] = row["publisher_article_url"]
        article["sources"]["abstract_en"] = next(
            x["abstract_source_url"] for x in official_evidence["items"]
            if x["doi"] == row["doi"]
        )
        article["source_url"] = row["publisher_article_url"]

    candidate["retrieved_at"] = retrieved_at
    candidate = normalize_issue_content(candidate)
    stamp_issue_readiness(candidate)
    validate_issue(candidate)
    if candidate["source_status"] != "official_verified":
        raise ValueError("official TOC authority was lost")
    evidence_report["official_publisher_toc_observed_on"] = original_day
    evidence_report["crossref_retrieved_at"] = retrieved_at
    evidence_report["scope"] = "isolated-b8-content-candidate-no-production-writes"
    return candidate, evidence_report


def make_report(
    candidate: dict[str, Any], evidence: dict[str, Any], *,
    translation_report: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "issue_id": candidate["issue_id"],
        "publisher_ordered_articles": len(candidate["articles"]),
        "source_status": candidate["source_status"],
        "content_status": candidate["content_status"],
        "publication_state": candidate["publication_state"],
        "translation_semantics_valid": issue_translation_semantics_valid(candidate),
        "content_quality_ready": (
            candidate["publication_state"] == "ready"
            and is_archivable_snapshot(candidate)
            and issue_translation_semantics_valid(candidate)
        ),
        "translation": translation_report,
        "abstract_source": "publisher-deposited-Crossref",
        "publisher_toc_source": evidence["articles"][0]["official_issue_source"],
        "rights_claim": "not_adjudicated_by_this_staging_script",
        "production_release_approved": False,
        "production_writes": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", type=Path, default=STAGE)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--translate", action="store_true")
    p.add_argument("--require-ready", action="store_true")
    p.add_argument("--timeout", type=int, default=15)
    args = p.parse_args()
    if args.require_ready and not args.translate:
        p.error("--require-ready requires --translate")

    stage = json.loads(args.stage.read_text(encoding="utf-8"))
    session = requests.Session()
    deposits = fetch_deposits(session, stage, timeout=args.timeout)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    candidate, evidence = make_candidate(stage, deposits, retrieved_at=now)
    translated = None
    if args.translate:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        cache = args.output_dir / "jae-41-6.translation-cache.json"
        translated = translate_missing(candidate, cache, max_translations=12)
        candidate = apply_translation_cache(candidate, cache_path=cache)
        candidate = normalize_issue_content(candidate)
        validate_issue(candidate)
    result = make_report(candidate, evidence, translation_report=translated)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (
        ("jae-41-6.candidate.json", candidate),
        ("jae-41-6.source-audit.json", evidence),
        ("report.json", result),
    ):
        (args.output_dir / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["content_quality_ready"] or not args.require_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
