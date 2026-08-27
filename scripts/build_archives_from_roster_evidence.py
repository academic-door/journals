"""Build historical issue archives from generic official roster evidence.

The ScienceDirect evidence flow builds archives directly from browser
snapshots.  For Wiley, Chicago, MIT Press and Cambridge rosters there is no
equivalent builder, so source_pending issues that have no archive at all can
never be promoted.  This script constructs a candidate issue from an official
roster (DOI + title + order), enriches authors and English abstracts from
Crossref / Semantic Scholar / OpenAlex, translates with the shared DeepSeek
pipeline, and archives the issue only when every content gate passes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.article_types import (  # noqa: E402
    PUBLISHABLE_TYPES,
    canonical_article_type,
    evidence_roster_article_type,
    exclusion_reason,
    is_publishable_type,
    normalize_issue_taxonomy,
    requires_abstract,
)
from collectors.metadata_fallback import (  # noqa: E402
    CROSSREF_API,
    MONTHS_BY_ISSUE,
    _authors,
    _clean_markup,
    _crossref_items,
    _date_year,
    _get_json,
    _openalex_metadata,
    _publication_date,
    _semantic_scholar_metadata_batch,
)
from scripts.import_official_roster_evidence import (  # noqa: E402
    validate_evidence,
)
from scripts.translate_issue import translate_missing  # noqa: E402
from scripts.update_journals import (  # noqa: E402
    JOURNALS_PATH,
    TRANSLATION_CACHE,
    apply_translation_cache,
    archive_issue,
    is_archivable_snapshot,
    normalize_issue_content,
    public_issue_path,
    stamp_issue_readiness,
    validate_issue,
)

DEFAULT_EVIDENCE_ROOT = ROOT / "data" / "provenance" / "official-rosters"
DEFAULT_API_ROOT = ROOT / "public" / "api" / "v1"
DEFAULT_STATE_ROOT = ROOT / "data" / "backfill-state"
DEFAULT_STAGING_ROOT = ROOT / "data" / "backfill-staging"


def _split_volume_issue(issue_id: str, journal_id: str) -> tuple[str, str]:
    prefix = f"{journal_id}-"
    rest = issue_id[len(prefix):] if issue_id.startswith(prefix) else issue_id
    parts = rest.rsplit("-", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"cannot parse volume/issue from {issue_id}")
    return parts[0], parts[1]


def _abstract_from_crossref(item: dict[str, Any]) -> str:
    abstract = str(item.get("abstract", "") or "")
    if not abstract:
        return ""
    return _clean_markup(re.sub(r"<[^>]+>", " ", abstract)).strip()


def _published_parts(item: dict[str, Any]) -> tuple[int, int]:
    """Return (year, month) from a Crossref record, month 0 when unknown."""

    for key in ("published-print", "published", "issued", "published-online"):
        parts = item.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            year = int(parts[0][0])
            month = int(parts[0][1]) if len(parts[0]) >= 2 else 0
            if year:
                return year, month
    return 0, 0


def _crossref_direct(
    session: requests.Session,
    doi: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    """Resolve one DOI against Crossref's works endpoint.

    The journal-wide dump used by `_crossref_map` is capped at one page
    (rows=500), which silently drops DOIs for high-volume journals.  A direct
    lookup by DOI is always complete regardless of pagination.
    """

    doi = str(doi or "").strip()
    if not doi:
        return {}
    url = f"{CROSSREF_API}/works/{doi}"
    try:
        payload = _get_json(session, url, timeout=timeout, attempts=2)
    except Exception:
        return {}
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    if not isinstance(message, dict):
        return {}
    return {
        "authors": _authors(message),
        "abstract": _abstract_from_crossref(message),
        "title": str(message.get("title", [""])[0] if message.get("title") else ""),
        "year": _date_year(message),
        "published": _published_parts(message),
    }


def _crossref_map(
    session: requests.Session,
    issn: str,
    *,
    start_year: int,
    timeout: int,
) -> dict[str, dict[str, Any]]:
    try:
        items = _crossref_items(
            issn,
            session=session,
            timeout=timeout,
            start_year=start_year,
        )
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        doi = str(item.get("DOI", "")).strip().casefold()
        if not doi:
            continue
        result[doi] = {
            "authors": _authors(item),
            "abstract": _abstract_from_crossref(item),
            "title": str(item.get("title", [""])[0] if item.get("title") else ""),
            "year": _date_year(item),
            "published": _published_parts(item),
        }
    return result


def _metadata_for_dois(
    session: requests.Session,
    dois: list[str],
    crossref: dict[str, dict[str, Any]],
    *,
    timeout: int,
) -> dict[str, dict[str, Any]]:
    """Return authors/abstract/title per DOI.

    Backfill order: direct Crossref lookup (complete even when the journal
    dump is truncated), Semantic Scholar batch, then OpenAlex per-DOI.
    """
    metadata: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for doi in dois:
        entry = crossref.get(doi, {})
        metadata[doi] = entry
        if not entry.get("abstract") or not entry.get("authors"):
            missing.append(doi)

    if missing:
        still_missing: list[str] = []
        for doi in missing:
            direct = _crossref_direct(session, doi, timeout=timeout)
            if direct:
                current = metadata[doi]
                if not current.get("authors") and direct.get("authors"):
                    current["authors"] = list(direct["authors"])
                if not current.get("abstract") and direct.get("abstract"):
                    current["abstract"] = str(direct["abstract"])
                if not current.get("title") and direct.get("title"):
                    current["title"] = str(direct["title"])
                if not current.get("year") and direct.get("year"):
                    current["year"] = str(direct["year"])
                if not current.get("published") and direct.get("published"):
                    current["published"] = tuple(direct["published"])
            if not metadata[doi].get("abstract") or not metadata[doi].get("authors"):
                still_missing.append(doi)
        missing = still_missing

    if missing:
        try:
            semantic = _semantic_scholar_metadata_batch(
                session, missing, timeout=timeout
            )
        except Exception:
            semantic = {}
        for doi, entry in semantic.items():
            current = metadata.setdefault(doi, {})
            if not current.get("authors") and entry.get("authors"):
                current["authors"] = list(entry["authors"])
            if not current.get("abstract") and entry.get("abstract"):
                current["abstract"] = str(entry["abstract"])
            if not current.get("title") and entry.get("title"):
                current["title"] = str(entry["title"])
            if not current.get("published") and entry.get("published"):
                current["published"] = tuple(entry["published"])
        missing = [
            doi
            for doi in missing
            if not metadata[doi].get("abstract") or not metadata[doi].get("authors")
        ]

    if missing:
        for doi in missing:
            try:
                authors, abstract, _url = _openalex_metadata(
                    session, doi, timeout=timeout
                )
            except Exception:
                continue
            current = metadata.setdefault(doi, {})
            if not current.get("authors") and authors:
                current["authors"] = list(authors)
            if not current.get("abstract") and abstract:
                current["abstract"] = str(abstract)
    return metadata


def build_candidate_from_evidence(
    evidence: dict[str, Any],
    journal: dict[str, Any],
    metadata: dict[str, dict[str, Any]],
    *,
    publication_date: str,
) -> dict[str, Any]:
    official_url = str(evidence["official_url"])
    journal_id = str(evidence["journal_id"])
    issue_id = str(evidence["issue_id"])
    volume, issue = _split_volume_issue(issue_id, journal_id)

    articles: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    missing_authors: list[str] = []
    missing_abstracts: list[str] = []

    def record_from_item(item: dict[str, Any], sequence: int) -> dict[str, Any]:
        doi = str(item["doi"]).strip().casefold()
        meta = metadata.get(doi, {})
        title = str(item.get("title_en", "")).strip() or str(
            meta.get("title", "")
        ).strip()
        authors = list(meta.get("authors", []))
        abstract = str(meta.get("abstract", "")).strip()
        article_type = evidence_roster_article_type(title)
        if article_type in PUBLISHABLE_TYPES and not authors:
            missing_authors.append(doi)
        if requires_abstract(article_type) and not abstract:
            missing_abstracts.append(doi)
        return {
            "paper_id": f"doi:{doi}",
            "sequence": sequence,
            "source_sequence": sequence,
            "article_type": article_type,
            "title_en": title,
            "title_cn": "",
            "authors": authors,
            "abstract_en": abstract,
            "abstract_cn": "",
            "doi": doi,
            "source_url": official_url,
            "publication_date": publication_date,
            "sources": {
                "issue": official_url,
                "roster": official_url,
                "metadata": f"https://doi.org/{doi}",
                "abstract_en": "crossref-or-semantic-scholar",
            },
            "translation": {"status": "missing"},
            "quality_flags": ["title_cn_missing", "abstract_cn_missing"],
        }

    for expected, item in enumerate(evidence.get("items", []), start=1):
        record = record_from_item(item, expected)
        if not is_publishable_type(record["article_type"]):
            excluded.append(
                {**record, "reason": exclusion_reason(record["article_type"])}
            )
            continue
        articles.append(record)

    for item in evidence.get("excluded_items", []):
        doi = str(item.get("doi", "") or item.get("source_id", "")).strip()
        excluded.append(
            {
                "title_en": str(item.get("title_en", "")).strip(),
                "doi": doi,
                "reason": str(item.get("reason", "excluded")),
            }
        )

    candidate: dict[str, Any] = {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": journal_id,
        "journal_name": str(journal.get("name", "")),
        "volume": volume,
        "issue": issue,
        "issue_label": f"Vol. {volume}",
        "publication_date": publication_date,
        "source_url": official_url,
        "retrieved_at": str(evidence.get("captured_at", "")),
        "expected_article_count": len(articles),
        "research_article_count": len(articles),
        "status": "incomplete",
        "publication_state": "enriching",
        "development_sample": False,
        "articles": articles,
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_authority": "official-issue-page",
            "roster_transport": str(evidence.get("method", "official-page-read")),
            "official_item_count": len(evidence.get("items", []))
            + len(evidence.get("excluded_items", [])),
            "excluded_items": excluded,
            "doi_complete": len(articles),
            "authors_complete": len(articles) - len(missing_authors),
            "abstract_en_complete": len(articles) - len(missing_abstracts),
            "translation_complete": 0,
            "duplicate_count": 0,
            "flags": ["translation_incomplete"],
        },
    }
    normalize_issue_taxonomy(candidate)
    validate_issue(candidate)
    return candidate


def process_evidence(
    evidence_path: Path,
    journal: dict[str, Any],
    *,
    session: requests.Session,
    api_root: Path,
    state_root: Path,
    staging_root: Path,
    translation_cache_root: Path,
    max_translations: int,
    start_year: int,
    timeout: int,
) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence)
    issue_id = str(evidence["issue_id"])
    journal_id = str(evidence["journal_id"])
    target = (
        api_root / "journals" / journal_id / "issues" / f"{issue_id}.json"
    )
    if target.exists():
        return {"issue_id": issue_id, "result": "already-archived"}

    volume, issue = _split_volume_issue(issue_id, journal_id)
    crossref = _crossref_map(
        session,
        str(journal.get("issn", "")),
        start_year=start_year,
        timeout=timeout,
    )
    dois = [
        str(item.get("doi", "")).strip().casefold()
        for item in evidence.get("items", [])
        if item.get("doi")
    ]
    metadata = _metadata_for_dois(session, dois, crossref, timeout=timeout)

    items = list(crossref.values()) if crossref else []
    issn = str(journal.get("issn", ""))
    publication_date = _publication_date(issn, volume, issue, items)
    if not publication_date or re.fullmatch(r"\d{4}", publication_date):
        year = Counter(
            str(meta.get("year", "")) for meta in metadata.values() if meta.get("year")
        ).most_common(1)
        year = year[0][0] if year else ""
        month_name = MONTHS_BY_ISSUE.get(issn, {}).get(issue, "")
        if not month_name:
            months = [
                int(meta.get("published", (0, 0))[1])
                for meta in metadata.values()
                if meta.get("published", (0, 0))[1]
            ]
            if months:
                month_name = datetime(2000, Counter(months).most_common(1)[0][0], 1).strftime(
                    "%B"
                )
        if month_name and year:
            publication_date = f"{month_name} {year}"
        elif year:
            publication_date = year
        else:
            publication_date = ""

    candidate = build_candidate_from_evidence(
        evidence,
        journal,
        metadata,
        publication_date=publication_date,
    )
    cache_path = translation_cache_root / f"{journal_id}.json"
    if candidate["articles"]:
        report = translate_missing(
            candidate,
            cache_path,
            max_translations=max_translations,
        )
        candidate = apply_translation_cache(candidate, cache_path=cache_path)
    else:
        report = {"translated": 0, "failed": []}

    candidate = normalize_issue_content(candidate)
    stamp_issue_readiness(candidate)
    validate_issue(candidate)

    if not is_archivable_snapshot(candidate):
        staging = staging_root / journal_id / f"{issue_id}.json"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "issue_id": issue_id,
            "result": candidate.get("publication_state", "blocked"),
            "missing_authors": int(
                candidate["quality"].get("authors_complete", 0)
            )
            != int(candidate.get("research_article_count", 0)),
            "missing_abstracts": int(
                candidate["quality"].get("abstract_en_complete", 0)
            )
            != int(candidate.get("research_article_count", 0)),
            "translated": int(
                candidate["quality"].get("translation_complete", 0)
            ),
            "staging": str(staging),
        }

    archived = archive_issue(candidate, api_root=api_root, replace_non_ready=True)
    if archived is None:
        return {"issue_id": issue_id, "result": "archive-gate-failed"}
    try:
        from scripts.import_official_roster_evidence import reconcile_state_files

        reconcile_state_files(candidate, state_root)
    except Exception:
        pass
    return {
        "issue_id": issue_id,
        "result": "ready",
        "archived": str(archived),
        "translated": int(candidate["quality"].get("translation_complete", 0)),
        "translation_report": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--journals-config", type=Path, default=JOURNALS_PATH)
    parser.add_argument("--api-root", type=Path, default=DEFAULT_API_ROOT)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--staging-root", type=Path, default=DEFAULT_STAGING_ROOT)
    parser.add_argument("--translation-cache-root", type=Path, default=TRANSLATION_CACHE)
    parser.add_argument("--max-translations", type=int, default=120)
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--issue-ids", default="")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args()

    configs = yaml.safe_load(args.journals_config.read_text(encoding="utf-8"))[
        "journals"
    ]
    wanted = {
        value.strip().casefold()
        for value in args.issue_ids.split(",")
        if value.strip()
    }
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "AcademicDoorJournals/1.0 "
                "(history recovery; mailto:academic-door@example.com)"
            ),
            "Accept": "application/json",
        }
    )
    results: list[dict[str, Any]] = []
    for evidence_path in sorted(args.evidence_root.rglob("*.json")):
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            issue_id = str(evidence.get("issue_id", ""))
            if wanted and issue_id.casefold() not in wanted:
                continue
            journal = configs.get(str(evidence.get("journal_id", "")).upper())
            if not journal:
                results.append(
                    {"issue_id": issue_id, "result": "unknown-journal"}
                )
                continue
            results.append(
                process_evidence(
                    evidence_path,
                    journal,
                    session=session,
                    api_root=args.api_root,
                    state_root=args.state_root,
                    staging_root=args.staging_root,
                    translation_cache_root=args.translation_cache_root,
                    max_translations=args.max_translations,
                    start_year=args.start_year,
                    timeout=args.timeout,
                )
            )
        except Exception as error:
            results.append(
                {"issue_id": issue_id, "result": "error", "error": str(error)}
            )
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "generated_at": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
