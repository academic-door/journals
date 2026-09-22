"""Recover trusted UChicago staging issues with publisher-supplied RePEc abstracts.

This helper never establishes issue membership or ordering. It only enriches an
explicitly requested staging payload that already carries official UChicago
roster authority and browser-authorized transport.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.article_types import is_publishable_type, requires_abstract  # noqa: E402
from collectors.metadata_fallback import _repec_abstract  # noqa: E402
from scripts.build_archives_from_roster_evidence import _usable_metadata_abstract  # noqa: E402
from scripts.import_official_roster_evidence import reconcile_state_files  # noqa: E402
from scripts.translate_issue import translate_missing  # noqa: E402
from scripts.update_journals import (  # noqa: E402
    JOURNALS_PATH,
    TRANSLATION_CACHE,
    apply_translation_cache,
    archive_issue,
    is_archivable_snapshot,
    normalize_issue_content,
    validate_issue,
)

DEFAULT_API_ROOT = ROOT / "public" / "api" / "v1"
DEFAULT_STATE_ROOT = ROOT / "data" / "backfill-state"
DEFAULT_STAGING_ROOT = ROOT / "data" / "backfill-staging"
ISSUE_ID = re.compile(r"^[A-Za-z0-9-]+$")


def trusted_chicago_staging(candidate: dict[str, Any], journal: dict[str, Any]) -> bool:
    quality = candidate.get("quality", {})
    return (
        str(journal.get("id", "")).strip().casefold()
        == str(candidate.get("journal_id", "")).strip().casefold()
        and str(journal.get("publisher", "")).strip() == "University of Chicago Press"
        and str(candidate.get("source_status", "")).strip() == "official_verified"
        and quality.get("roster_match") is True
        and quality.get("order_preserved") is True
        and str(quality.get("roster_authority", "")).strip() == "official-issue-page"
        and str(quality.get("roster_transport", "")).strip() == "browser-authorized"
        and "journals.uchicago.edu/" in str(candidate.get("source_url", "")).casefold()
    )


def enrich_missing_repec_abstracts(
    candidate: dict[str, Any],
    journal: dict[str, Any],
    *,
    session: requests.Session,
    timeout: int,
) -> int:
    if not trusted_chicago_staging(candidate, journal):
        return 0
    series_code = str(journal.get("repec_series_code", "")).strip()
    if not series_code:
        return 0
    if any(
        is_publishable_type(str(article.get("article_type", "")))
        and not article.get("authors")
        for article in candidate.get("articles", [])
    ):
        return 0

    filled = 0
    for article in candidate.get("articles", []):
        if not requires_abstract(str(article.get("article_type", ""))):
            continue
        if str(article.get("abstract_en", "")).strip():
            continue
        doi = str(article.get("doi", "")).strip().casefold()
        if not doi:
            continue
        try:
            abstract, repec_url = _repec_abstract(
                session,
                doi,
                timeout=timeout,
                series_code=series_code,
            )
        except Exception:
            continue
        abstract = _usable_metadata_abstract(abstract)
        if not abstract:
            continue
        article["abstract_en"] = abstract
        sources = article.setdefault("sources", {})
        sources["abstract_en"] = repec_url
        sources["repec"] = repec_url
        filled += 1
    return filled


def recover_one(
    staging_path: Path,
    journal: dict[str, Any],
    *,
    session: requests.Session,
    api_root: Path,
    state_root: Path,
    translation_cache_root: Path,
    max_translations: int,
    timeout: int,
) -> dict[str, Any]:
    candidate = json.loads(staging_path.read_text(encoding="utf-8"))
    issue_id = str(candidate.get("issue_id", "")).strip()
    journal_id = str(candidate.get("journal_id", "")).strip()
    target = api_root / "journals" / journal_id / "issues" / f"{issue_id}.json"
    if target.exists():
        return {"issue_id": issue_id, "result": "already-archived"}
    if not trusted_chicago_staging(candidate, journal):
        return {"issue_id": issue_id, "result": "staging-not-authoritative"}

    filled = enrich_missing_repec_abstracts(
        candidate, journal, session=session, timeout=timeout
    )
    if not filled:
        return {"issue_id": issue_id, "result": "repec-no-progress"}

    candidate = normalize_issue_content(candidate)
    cache_path = translation_cache_root / f"{journal_id}.json"
    report = translate_missing(
        candidate,
        cache_path,
        max_translations=max_translations,
    )
    candidate = apply_translation_cache(candidate, cache_path=cache_path)
    candidate = normalize_issue_content(candidate)
    validate_issue(candidate)
    staging_path.write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if not is_archivable_snapshot(candidate):
        return {
            "issue_id": issue_id,
            "result": candidate.get("publication_state", "blocked"),
            "repec_abstracts_filled": filled,
            "translation_report": report,
        }

    archived = archive_issue(candidate, api_root=api_root, replace_non_ready=True)
    if archived is None:
        return {
            "issue_id": issue_id,
            "result": "archive-gate-failed",
            "repec_abstracts_filled": filled,
            "translation_report": report,
        }
    reconcile_state_files(candidate, state_root)
    return {
        "issue_id": issue_id,
        "result": "ready",
        "repec_abstracts_filled": filled,
        "translated": int(candidate["quality"].get("translation_complete", 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-ids", required=True)
    parser.add_argument("--journals-config", type=Path, default=JOURNALS_PATH)
    parser.add_argument("--api-root", type=Path, default=DEFAULT_API_ROOT)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--staging-root", type=Path, default=DEFAULT_STAGING_ROOT)
    parser.add_argument("--translation-cache-root", type=Path, default=TRANSLATION_CACHE)
    parser.add_argument("--max-translations", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()

    wanted = [value.strip() for value in args.issue_ids.split(",") if value.strip()]
    if any(not ISSUE_ID.fullmatch(value) for value in wanted):
        raise ValueError("invalid issue id")
    configs = yaml.safe_load(args.journals_config.read_text(encoding="utf-8"))["journals"]
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "AcademicDoorJournals/1.0 (history recovery)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )

    results: list[dict[str, Any]] = []
    for issue_id in wanted:
        matches = list(args.staging_root.rglob(f"{issue_id}.json"))
        if len(matches) != 1:
            continue
        candidate = json.loads(matches[0].read_text(encoding="utf-8"))
        journal = configs.get(str(candidate.get("journal_id", "")).upper())
        if not journal:
            results.append({"issue_id": issue_id, "result": "unknown-journal"})
            continue
        results.append(
            recover_one(
                matches[0],
                journal,
                session=session,
                api_root=args.api_root,
                state_root=args.state_root,
                translation_cache_root=args.translation_cache_root,
                max_translations=args.max_translations,
                timeout=args.timeout,
            )
        )

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
