"""Recover trusted Elsevier staging issues without changing roster authority.

This helper consumes only explicitly requested staging payloads that already
carry accepted publisher-family roster/order authority. It may fill missing
article abstracts from official Elsevier metadata and run the existing
translation pipeline; it never establishes or upgrades issue membership/order.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.article_types import (  # noqa: E402
    OFFICIAL_NO_ABSTRACT_STATUS,
    is_publishable_type,
    official_no_abstract_exception,
    requires_abstract,
)
from collectors.metadata_fallback import _elsevier_lookup  # noqa: E402
from scripts.build_archives_from_roster_evidence import (  # noqa: E402
    _metadata_for_dois,
    _usable_metadata_abstract,
)
from scripts.build_sciencedirect_browser_archives import pii_from_href  # noqa: E402
from scripts.import_official_roster_evidence import reconcile_state_files  # noqa: E402
from scripts.translate_issue import translate_missing  # noqa: E402
from scripts.update_journals import (  # noqa: E402
    JOURNALS_PATH,
    TRANSLATION_CACHE,
    apply_translation_cache,
    archive_issue,
    is_archivable_snapshot,
    issue_source_status,
    normalize_issue_content,
    validate_issue,
)

DEFAULT_API_ROOT = ROOT / "public" / "api" / "v1"
DEFAULT_STATE_ROOT = ROOT / "data" / "backfill-state"
DEFAULT_STAGING_ROOT = ROOT / "data" / "backfill-staging"
ISSUE_ID = re.compile(r"^[A-Za-z0-9-]+$")


def configured_repec_series_code(journal: dict[str, Any]) -> str:
    """Return the configured RePEc series code without granting roster authority."""

    raw = str(journal.get("repec_series_url", "")).strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname != "ideas.repec.org":
        return ""
    path = parsed.path.strip("/")
    if not path.startswith("s/") or not path.endswith(".html"):
        return ""
    return path[len("s/") : -len(".html")]


def trusted_elsevier_staging(
    candidate: dict[str, Any],
    journal: dict[str, Any],
) -> bool:
    quality = candidate.get("quality", {})
    flags = {str(flag) for flag in quality.get("flags", [])}
    authority = str(quality.get("roster_authority", "")).strip()
    transport = str(quality.get("roster_transport", "")).strip()
    source_status = issue_source_status(candidate)
    accepted_authority = (
        (
            source_status == "official_verified"
            and authority == "official-issue-page"
            and transport == "browser-authorized"
        )
        or (
            source_status == "publisher_verified"
            and authority == "repec-publisher-supplied"
            and transport == "repec-serial-page"
        )
    )
    return (
        str(journal.get("id", "")).strip().casefold()
        == str(candidate.get("journal_id", "")).strip().casefold()
        and str(journal.get("publisher", "")).strip() == "Elsevier"
        and quality.get("roster_match") is True
        and quality.get("order_preserved") is True
        and accepted_authority
        and "crossref_provisional_roster" not in flags
        and authority not in {"crossref", "crossref-provisional"}
        and not transport.startswith("crossref")
    )


def enrich_missing_elsevier_abstracts(
    candidate: dict[str, Any],
    journal: dict[str, Any],
    *,
    session: requests.Session,
    timeout: int,
) -> int:
    """Fill only missing abstracts while preserving the accepted issue roster.

    Official Elsevier article metadata remains first choice. When it has no
    usable abstract, reuse the repository's established DOI metadata fallback
    chain (Crossref -> Semantic Scholar -> OpenAlex -> configured RePEc). Only abstract_en and
    its field-level provenance may change; roster/order, authors, title and
    issue source authority remain untouched.
    """

    if not trusted_elsevier_staging(candidate, journal):
        return 0
    if any(
        is_publishable_type(str(article.get("article_type", "")))
        and not article.get("authors")
        for article in candidate.get("articles", [])
    ):
        return 0

    filled = 0
    unresolved: list[tuple[dict[str, Any], str]] = []
    for article in candidate.get("articles", []):
        if not requires_abstract(str(article.get("article_type", ""))):
            continue
        if str(article.get("abstract_en", "")).strip():
            continue
        doi = str(article.get("doi", "")).strip().casefold()
        if not doi:
            continue
        pii = pii_from_href(article.get("source_url"))
        try:
            lookup = _elsevier_lookup(
                session,
                pii,
                doi=doi,
                timeout=timeout,
            )
        except Exception:
            lookup = {}
        abstract = _usable_metadata_abstract(lookup.get("abstract", ""))
        if abstract:
            article["abstract_en"] = abstract
            sources = article.setdefault("sources", {})
            sources["abstract_en"] = "official-elsevier-metadata"
            source_url = str(lookup.get("source_url", "")).strip()
            if source_url:
                sources["abstract_en_url"] = source_url
            filled += 1
            continue
        unresolved.append((article, doi))

    if not unresolved:
        return filled

    fallback = _metadata_for_dois(
        session,
        list(dict.fromkeys(doi for _article, doi in unresolved)),
        {},
        timeout=timeout,
        repec_series_code=configured_repec_series_code(journal),
    )
    for article, doi in unresolved:
        metadata = fallback.get(doi, {})
        abstract = _usable_metadata_abstract(metadata.get("abstract", ""))
        if not abstract:
            continue
        article["abstract_en"] = abstract
        sources = article.setdefault("sources", {})
        source_name = str(metadata.get("abstract_source", "")).strip()
        source_url = str(metadata.get("abstract_url", "")).strip()
        sources["abstract_en"] = source_url or source_name or "crossref-or-semantic-scholar"
        if source_url:
            sources["abstract_en_url"] = source_url
        filled += 1
    return filled


def apply_official_no_abstract_exceptions(candidate: dict[str, Any]) -> int:
    """Apply only issue+DOI allowlisted publisher no-abstract exceptions."""

    issue_id = str(candidate.get("issue_id", "")).strip()
    applied = 0
    for article in candidate.get("articles", []):
        if str(article.get("abstract_en", "")).strip():
            continue
        doi = str(article.get("doi", "")).strip().casefold()
        exception = official_no_abstract_exception(issue_id, doi)
        if not exception:
            continue

        article["title_cn"] = str(exception["title_cn"])
        article["abstract_en"] = ""
        article["abstract_cn"] = ""
        article["abstract_status"] = OFFICIAL_NO_ABSTRACT_STATUS
        article["abstract_note"] = str(exception["abstract_note"])
        sources = article.setdefault("sources", {})
        sources["abstract_en"] = "publisher-page-no-standalone-abstract"
        source_url = str(exception.get("source_url", "") or article.get("source_url", "")).strip()
        if source_url:
            sources["abstract_en_url"] = source_url
        article["translation"] = {
            "status": "complete",
            "provider": "manual-official-title",
            "prompt_version": "official-no-abstract-v1",
        }
        article["quality_flags"] = [
            flag
            for flag in article.get("quality_flags", [])
            if flag not in {"title_cn_missing", "abstract_en_missing", "abstract_cn_missing"}
        ]
        article["quality_flags"].append("official_abstract_unavailable")
        article["quality_flags"] = list(dict.fromkeys(article["quality_flags"]))
        applied += 1
    return applied


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
    if not trusted_elsevier_staging(candidate, journal):
        return {"issue_id": issue_id, "result": "staging-not-authoritative"}
    if any(
        is_publishable_type(str(article.get("article_type", "")))
        and not article.get("authors")
        for article in candidate.get("articles", [])
    ):
        return {"issue_id": issue_id, "result": "staging-authors-incomplete"}

    filled = enrich_missing_elsevier_abstracts(
        candidate,
        journal,
        session=session,
        timeout=timeout,
    )
    no_abstract_applied = apply_official_no_abstract_exceptions(candidate)
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
            "elsevier_abstracts_filled": filled,
            "official_no_abstract_exceptions_applied": no_abstract_applied,
            "translation_report": report,
        }

    archived = archive_issue(candidate, api_root=api_root, replace_non_ready=True)
    if archived is None:
        return {
            "issue_id": issue_id,
            "result": "archive-gate-failed",
            "elsevier_abstracts_filled": filled,
            "official_no_abstract_exceptions_applied": no_abstract_applied,
            "translation_report": report,
        }
    reconcile_state_files(candidate, state_root)
    return {
        "issue_id": issue_id,
        "result": "ready",
        "elsevier_abstracts_filled": filled,
        "official_no_abstract_exceptions_applied": no_abstract_applied,
        "translated": int(candidate["quality"].get("translation_complete", 0)),
        "translation_report": report,
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
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
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
