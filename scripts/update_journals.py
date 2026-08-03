from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.aea import fetch_current_issue as fetch_aea
from collectors.article_types import (
    abstract_is_complete,
    canonical_issue_label,
    normalize_issue_taxonomy,
    translation_is_complete,
)
from scripts.china_relevance import annotate_issue, classify_china_relevance
from scripts.translate_issue import (
    TranslationError,
    _source_hash,
    translate_missing,
    validate_translation,
)


PUBLIC_API = ROOT / "public" / "api" / "v1"
SCHEMA_PATH = ROOT / "schemas" / "issue.schema.json"
JOURNALS_PATH = ROOT / "config" / "journals.yml"
TRANSLATION_CACHE = ROOT / "data" / "translation-cache"
ARTICLE_TYPE_OVERRIDES_PATH = ROOT / "data" / "article-type-overrides.json"
COMMENT_TITLE_OVERRIDES = {
    "10.1086/740225": "国家起源：土地生产率还是可攫取性？——评论",
}
UPDATE_REPORT = ROOT / "output" / "journal-update-report.json"
ARCHIVE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)


class SourceLagError(RuntimeError):
    """The detector is ahead of the publisher's current-issue endpoint."""


ABSTRACT_LABEL_PATTERN = re.compile(
    r"^\s*(?:Abstract|摘要)\s*(?:[:：.．—-]\s*)?",
    re.IGNORECASE,
)
ABSTRACT_PLACEHOLDERS = {
    "international audience",
    "n/a",
    "none",
    "null",
    "please provide abstract",
    "no abstract available",
    "no abstract is available",
    "no abstract is available for this item",
}


def clean_abstract_label(value: Any) -> str:
    """Remove publisher labels and known non-abstract placeholders."""

    text = str(value or "").strip()
    cleaned = ABSTRACT_LABEL_PATTERN.sub("", text, count=1).strip()
    if cleaned.casefold().rstrip(".") in ABSTRACT_PLACEHOLDERS:
        return ""
    return cleaned


def comment_without_abstract(article: dict[str, Any]) -> bool:
    return article.get("article_type") == "comment" and not article.get("abstract_en")


def article_type_overrides() -> dict[str, str]:
    try:
        payload = json.loads(ARTICLE_TYPE_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_issue_content(issue: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic taxonomy, quality counts, and reader cleanup."""

    for article in issue.get("articles", []):
        article["abstract_en"] = clean_abstract_label(article.get("abstract_en"))
        article["abstract_cn"] = clean_abstract_label(article.get("abstract_cn"))
    issue = normalize_issue_taxonomy(
        issue,
        overrides=article_type_overrides(),
    )
    issue["issue_label"] = canonical_issue_label(
        issue.get("volume"),
        issue.get("issue"),
        issue.get("issue_label"),
    )
    return annotate_issue(issue)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def validate_issue(issue: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(issue), key=lambda error: list(error.path))
    if errors:
        messages = [
            f"{'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors
        ]
        raise ValueError("Issue schema validation failed:\n" + "\n".join(messages))


def _translation_payload_is_current(
    article: dict[str, Any],
    translated: dict[str, Any],
) -> bool:
    if not translated.get("title_cn"):
        return False
    if article.get("abstract_en") and not translated.get("abstract_cn"):
        return False
    source_hash = str(translated.get("source_hash", ""))
    if source_hash and source_hash != _source_hash(article):
        return False
    try:
        validate_translation(
            article,
            {
                "title_cn": translated.get("title_cn", ""),
                "abstract_cn": clean_abstract_label(
                    translated.get("abstract_cn", "")
                ),
            },
        )
    except TranslationError:
        return False
    return True


def _clear_stale_translation(article: dict[str, Any]) -> None:
    article["title_cn"] = ""
    article["abstract_cn"] = ""
    article["translation"] = {"status": "missing"}
    flags = set(article.get("quality_flags", []))
    flags.add("title_cn_missing")
    if not comment_without_abstract(article):
        flags.add("abstract_cn_missing")
    article["quality_flags"] = list(flags)


def apply_translation_cache(
    issue: dict[str, Any],
    *,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    cache_path = cache_path or TRANSLATION_CACHE / f"{issue['journal_id']}.json"
    cache = read_json(cache_path) or {}
    for article in issue["articles"]:
        article["abstract_en"] = clean_abstract_label(article.get("abstract_en"))
        article["abstract_cn"] = clean_abstract_label(article.get("abstract_cn"))
        translated = cache.get(article.get("doi", ""), {})
        cache_hash = str(translated.get("source_hash", ""))
        cache_is_stale = bool(cache_hash and cache_hash != _source_hash(article))
        embedded_matches_cache = (
            article.get("title_cn") == translated.get("title_cn")
            and article.get("abstract_cn")
            == clean_abstract_label(translated.get("abstract_cn", ""))
        )
        if cache_is_stale and embedded_matches_cache:
            _clear_stale_translation(article)
        elif translation_is_complete(article):
            embedded = {
                "title_cn": article.get("title_cn", ""),
                "abstract_cn": article.get("abstract_cn", ""),
                "source_hash": article.get("translation", {}).get("source_hash", ""),
            }
            if not _translation_payload_is_current(article, embedded):
                _clear_stale_translation(article)

        if translated and not _translation_payload_is_current(article, translated):
            translated = {}
        if article.get("article_type") == "comment" and not article.get("title_cn"):
            override = COMMENT_TITLE_OVERRIDES.get(article.get("doi", ""))
            if override:
                article["title_cn"] = override
                article["quality_flags"] = [
                    flag for flag in article["quality_flags"] if flag != "title_cn_missing"
                ]
        if translated.get("title_cn"):
            article["title_cn"] = translated["title_cn"]
            article["quality_flags"] = [
                flag for flag in article["quality_flags"] if flag != "title_cn_missing"
            ]
        if translated.get("abstract_cn"):
            article["abstract_cn"] = clean_abstract_label(translated["abstract_cn"])
            article["quality_flags"] = [
                flag
                for flag in article["quality_flags"]
                if flag != "abstract_cn_missing"
            ]
        elif comment_without_abstract(article):
            article["quality_flags"] = [
                flag
                for flag in article["quality_flags"]
                if flag != "abstract_cn_missing"
            ]
        provenance = translated.get("translation", {})
        if provenance:
            article["translation"].update(provenance)
            article["translation"]["source_hash"] = translated.get(
                "source_hash", _source_hash(article)
            )
        if article["title_cn"] and article["abstract_cn"]:
            article["translation"]["status"] = "complete"
        elif comment_without_abstract(article) and article["title_cn"]:
            article["translation"]["status"] = "complete"
        elif article["title_cn"] or article["abstract_cn"]:
            article["translation"]["status"] = "partial"

    translation_complete = sum(
        translation_is_complete(article) for article in issue["articles"]
    )
    issue["quality"]["translation_complete"] = translation_complete
    flags = [
        flag for flag in issue["quality"]["flags"] if flag != "translation_incomplete"
    ]
    if translation_complete != issue["research_article_count"]:
        flags.append("translation_incomplete")
    issue["quality"]["flags"] = flags
    issue["status"] = "ready" if not flags else "incomplete"
    return issue


def collector_for(config: dict[str, Any]) -> Callable[[], dict[str, Any]]:
    collector = config["collector"]
    current_url = config["current_issue_url"]
    if collector == "aea":
        return lambda: fetch_aea(
            current_url,
            journal_id=config["id"],
            journal_name=config["name"],
        )
    if collector == "chicago":
        from collectors.chicago import fetch_current_issue

        return lambda: fetch_current_issue(
            current_url,
            journal_id=config["id"],
            journal_name=config["name"],
        )
    if collector == "oup":
        from collectors.oup import fetch_current_issue

        return lambda: fetch_current_issue(config["id"], current_url)
    if collector == "wiley":
        from collectors.wiley import fetch_current_issue

        return lambda: fetch_current_issue(
            current_url,
            journal_id=config["id"],
            journal_name=config["name"],
        )
    if collector == "elsevier":
        from collectors.elsevier import fetch_current_issue

        rss_url = config.get("rss_url") or (
            "https://rss.sciencedirect.com/publication/science/"
            f"{str(config['issn']).replace('-', '')}"
        )
        return lambda: fetch_current_issue(
            journal_id=config["id"],
            journal_name=config["name"],
            issn=str(config["issn"]),
            repec_series_url=config["repec_series_url"],
            issue_url_template=config["issue_url_template"],
            rss_url=rss_url,
            publication_lead_months=int(config.get("publication_lead_months", 1)),
            doi_template=config.get("doi_template", ""),
        )
    if collector == "crossref":
        from collectors.metadata_fallback import fetch_crossref_current_issue

        return lambda: fetch_crossref_current_issue(
            journal_id=config["id"],
            journal_name=config["name"],
            issn=str(config["issn"]),
            current_issue_url=current_url,
            repec_jpe=config.get("fallback") == "crossref-repec",
            repec_series_code=config.get("repec_series_code", ""),
        )
    if collector == "repec":
        from collectors.elsevier import fetch_current_issue

        return lambda: fetch_current_issue(
            journal_id=config["id"],
            journal_name=config["name"],
            issn=str(config["issn"]),
            repec_series_url=config["repec_series_url"],
            issue_url_template=config["current_issue_url"],
            doi_template=config.get("doi_template", ""),
        )
    raise ValueError(f"Unknown collector: {collector}")


def fallback_collector_for(config: dict[str, Any]) -> Callable[[], dict[str, Any]] | None:
    fallback = config.get("fallback", "")
    collectors: list[Callable[[], dict[str, Any]]] = []
    if config.get("rss_url"):
        from collectors.metadata_fallback import fetch_official_rss_issue

        collectors.append(
            lambda: fetch_official_rss_issue(
                journal_id=config["id"],
                journal_name=config["name"],
                issn=str(config["issn"]),
                current_issue_url=config["current_issue_url"],
                rss_url=config["rss_url"],
                repec_jpe=fallback == "crossref-repec",
                repec_series_code=config.get("repec_series_code", ""),
            )
        )
    if fallback == "repec":
        from collectors.elsevier import fetch_current_issue

        collectors.append(
            lambda: fetch_current_issue(
                journal_id=config["id"],
                journal_name=config["name"],
                issn=str(config["issn"]),
                repec_series_url=config["repec_series_url"],
                issue_url_template=config["issue_url_template"],
            )
        )
    elif fallback:
        from collectors.metadata_fallback import fetch_crossref_current_issue

        collectors.append(
            lambda: fetch_crossref_current_issue(
                journal_id=config["id"],
                journal_name=config["name"],
                issn=str(config["issn"]),
                current_issue_url=config["current_issue_url"],
                repec_jpe=fallback == "crossref-repec",
                repec_series_code=config.get("repec_series_code", ""),
            )
        )
    if not collectors:
        return None

    def chained_fallback() -> dict[str, Any]:
        errors: list[str] = []
        for collector in collectors:
            try:
                return collector()
            except Exception as error:
                errors.append(f"{type(error).__name__}: {error}")
        raise RuntimeError("; ".join(errors))

    return chained_fallback


def public_issue_path(journal_id: str) -> Path:
    return PUBLIC_API / "journals" / journal_id / "issues" / "current.json"


def detected_issue_path(journal_id: str) -> Path:
    return PUBLIC_API / "journals" / journal_id / "issues" / "detected.json"


def _issue_date_key(issue: dict[str, Any]) -> tuple[int, int]:
    """Return a comparable year/month key without trusting free-form labels."""

    value = str(issue.get("publication_date", "")).strip()
    for pattern in ("%B %Y", "%Y-%m-%d", "%Y/%m/%d", "%Y"):
        try:
            parsed = datetime.strptime(value, pattern)
        except ValueError:
            continue
        return parsed.year, parsed.month
    match = re.search(r"(20\d{2})[^0-9]+(1[0-2]|0?[1-9])", value)
    if match:
        return int(match.group(1)), int(match.group(2))
    year = re.search(r"20\d{2}", value)
    return (int(year.group(0)), 0) if year else (0, 0)


def _issue_number(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else -1


def issue_is_newer(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """Compare issue snapshots so a stale publisher endpoint cannot regress data."""

    candidate_date = _issue_date_key(candidate)
    baseline_date = _issue_date_key(baseline)
    if candidate_date != baseline_date and candidate_date != (0, 0) and baseline_date != (0, 0):
        return candidate_date > baseline_date
    candidate_volume = _issue_number(candidate.get("volume"))
    baseline_volume = _issue_number(baseline.get("volume"))
    if candidate_volume != baseline_volume and candidate_volume >= 0 and baseline_volume >= 0:
        return candidate_volume > baseline_volume
    candidate_issue = _issue_number(candidate.get("issue"))
    baseline_issue = _issue_number(baseline.get("issue"))
    return candidate_issue > baseline_issue


def is_detected_snapshot(issue: dict[str, Any]) -> bool:
    """Accept a confirmed issue roster even while abstracts are enriching."""

    article_count = len(issue.get("articles", []))
    research_count = int(issue.get("research_article_count", 0))
    quality = issue.get("quality", {})
    return (
        research_count > 0
        and article_count == research_count
        and int(issue.get("expected_article_count", 0)) == research_count
        and bool(quality.get("roster_match"))
        and bool(quality.get("order_preserved"))
        and int(quality.get("doi_complete", 0)) == research_count
        and int(quality.get("authors_complete", 0)) == research_count
        and int(quality.get("duplicate_count", 0)) == 0
        and "crossref_provisional_roster" not in quality.get("flags", [])
        and not any(
            str(flag).startswith("collector_error:")
            for flag in quality.get("flags", [])
        )
    )


def write_detected_snapshot(issue: dict[str, Any]) -> Path:
    """Publish the newest trustworthy roster without declaring it ready."""

    if not is_detected_snapshot(issue):
        raise ValueError("detected issue failed the roster quality gate")
    issue["publication_state"] = (
        "ready" if is_archivable_snapshot(issue) else "enriching"
    )
    target = detected_issue_path(str(issue["journal_id"]))
    write_json(target, issue)
    readback = read_json(target)
    if readback is None or readback.get("issue_id") != issue.get("issue_id"):
        raise RuntimeError("detected issue write-back verification failed")
    return target


def is_archivable_snapshot(issue: dict[str, Any]) -> bool:
    quality = issue.get("quality", {})
    count = int(issue.get("research_article_count", 0))
    return (
        is_publishable_snapshot(issue)
        and count > 0
        and int(quality.get("translation_complete", 0)) == count
    )


def archive_issue(
    issue: dict[str, Any],
    *,
    api_root: Path = PUBLIC_API,
) -> Path | None:
    """Save one immutable, fully validated issue snapshot."""

    journal_id = str(issue.get("journal_id", ""))
    issue_id = str(issue.get("issue_id", ""))
    if (
        not ARCHIVE_ID_PATTERN.fullmatch(journal_id)
        or not ARCHIVE_ID_PATTERN.fullmatch(issue_id)
        or not is_archivable_snapshot(issue)
    ):
        return None
    target = api_root / "journals" / journal_id / "issues" / f"{issue_id}.json"
    existing = read_json(target)
    if existing is not None:
        if existing.get("issue_id") != issue_id:
            raise RuntimeError(f"archive collision at {target}")
        return target
    write_json(target, issue)
    return target


def archived_issues(
    journal_id: str,
    *,
    api_root: Path = PUBLIC_API,
) -> list[dict[str, Any]]:
    folder = api_root / "journals" / journal_id / "issues"
    issues: list[dict[str, Any]] = []
    for path in folder.glob("*.json"):
        if path.name in {"current.json", "detected.json", "index.json"}:
            continue
        payload = read_json(path)
        if payload and payload.get("journal_id") == journal_id:
            issues.append(payload)
    return issues


def archive_publication_sort_key(issue: dict[str, Any]) -> tuple[int, int, int, int]:
    """Sort archived issues by real publication period, then volume and issue."""

    raw = str(issue.get("publication_date", "")).strip()
    parsed: datetime | None = None
    for pattern in ("%Y-%m-%d", "%Y-%m", "%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(raw, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        chinese = re.fullmatch(r"(\d{4})\s*年\s*(\d{1,2})\s*月", raw)
        if chinese:
            parsed = datetime(int(chinese.group(1)), int(chinese.group(2)), 1)
    volume = int(re.sub(r"\D", "", str(issue.get("volume", ""))) or 0)
    number = int(re.sub(r"\D", "", str(issue.get("issue", ""))) or 0)
    return (
        parsed.year if parsed else 0,
        parsed.month if parsed else 0,
        volume,
        number,
    )


def archive_issue_label(issue: dict[str, Any]) -> str:
    return canonical_issue_label(
        issue.get("volume"),
        issue.get("issue"),
        issue.get("issue_label"),
    )


def write_archive_index(
    journal_id: str,
    journal_name: str,
    *,
    updated_at: str,
    api_root: Path = PUBLIC_API,
) -> None:
    entries = []
    for issue in archived_issues(journal_id, api_root=api_root):
        entries.append(
            {
                "issue_id": issue["issue_id"],
                "volume": issue.get("volume", ""),
                "issue": issue.get("issue", ""),
                "issue_label": archive_issue_label(issue),
                "publication_date": issue.get("publication_date", ""),
                "retrieved_at": issue.get("retrieved_at", ""),
                "article_count": issue.get("research_article_count", 0),
                "url": (
                    f"/journals/api/v1/journals/{journal_id}/issues/"
                    f"{issue['issue_id']}.json"
                ),
            }
        )
    entries.sort(key=archive_publication_sort_key, reverse=True)
    write_json(
        api_root / "journals" / journal_id / "issues" / "index.json",
        {
            "schema_version": "1.0",
            "journal_id": journal_id,
            "journal_name": journal_name,
            "updated_at": updated_at,
            "issue_count": len(entries),
            "current_url": (
                f"/journals/api/v1/journals/{journal_id}/issues/current.json"
            ),
            "issues": entries,
        },
    )


def search_record(
    issue: dict[str, Any],
    article: dict[str, Any],
    journal: dict[str, Any],
) -> dict[str, Any]:
    relevance = article.get("china_relevance") or classify_china_relevance(article)
    return {
        "journal_id": issue["journal_id"],
        "journal_short_name": journal.get("short_name", issue["journal_id"].upper()),
        "journal_name": issue.get("journal_name", journal.get("name", "")),
        "field": journal.get("field", "general"),
        "tier": journal.get("tier", ""),
        "issue_id": issue["issue_id"],
        "volume": issue.get("volume", ""),
        "issue": issue.get("issue", ""),
        "issue_label": canonical_issue_label(
            issue.get("volume"), issue.get("issue"), issue.get("issue_label")
        ),
        "publication_date": issue.get("publication_date", ""),
        "sequence": article.get("sequence", 0),
        "paper_id": article.get("paper_id", ""),
        "title_en": article.get("title_en", ""),
        "title_cn": article.get("title_cn", ""),
        "authors": article.get("authors", []),
        "abstract_en": article.get("abstract_en", ""),
        "abstract_cn": article.get("abstract_cn", ""),
        "doi": article.get("doi", ""),
        "source_url": article.get("source_url", ""),
        "china_related": relevance.get("status") == "yes",
    }


def write_search_indexes(
    journal_configs: dict[str, dict[str, Any]],
    issues: dict[str, dict[str, Any]],
    *,
    updated_at: str,
    api_root: Path = PUBLIC_API,
) -> None:
    """Write lazy-loaded search datasets without embedding them in page HTML."""

    latest_records: list[dict[str, Any]] = []
    history_records: list[dict[str, Any]] = []
    issue_count = 0
    for key, config in journal_configs.items():
        if not config.get("enabled"):
            continue
        current = issues.get(key)
        if current and is_publishable_snapshot(current):
            latest_records.extend(
                search_record(current, article, config)
                for article in current.get("articles", [])
            )

        by_issue_id: dict[str, dict[str, Any]] = {}
        for archived in archived_issues(config["id"], api_root=api_root):
            if is_publishable_snapshot(archived):
                by_issue_id[archived["issue_id"]] = archived
        if current and is_publishable_snapshot(current):
            by_issue_id[current["issue_id"]] = current
        issue_count += len(by_issue_id)
        for issue in by_issue_id.values():
            history_records.extend(
                search_record(issue, article, config)
                for article in issue.get("articles", [])
            )

    def record_key(record: dict[str, Any]) -> tuple[str, str, int]:
        return (
            str(record.get("publication_date", "")),
            str(record.get("journal_id", "")),
            -int(record.get("sequence", 0)),
        )

    latest_records.sort(key=record_key, reverse=True)
    history_records.sort(key=record_key, reverse=True)
    search_root = api_root / "search"
    write_json(
        search_root / "latest.json",
        {
            "schema_version": "1.0",
            "scope": "latest",
            "updated_at": updated_at,
            "record_count": len(latest_records),
            "records": latest_records,
        },
    )
    write_json(
        search_root / "all.json",
        {
            "schema_version": "1.0",
            "scope": "all",
            "updated_at": updated_at,
            "issue_count": issue_count,
            "record_count": len(history_records),
            "records": history_records,
        },
    )
    write_json(
        search_root / "index.json",
        {
            "schema_version": "1.0",
            "updated_at": updated_at,
            "latest_count": len(latest_records),
            "history_count": len(history_records),
            "issue_count": issue_count,
            "latest_url": "/journals/api/v1/search/latest.json",
            "all_url": "/journals/api/v1/search/all.json",
        },
    )


def structural_flags(issue: dict[str, Any]) -> list[str]:
    content_only = {
        "translation_incomplete",
        # Provenance warnings remain visible in the status API, but they do not
        # invalidate an otherwise complete issue snapshot.
        "publisher_html_blocked_crossref_fallback",
        "publisher_html_blocked_sciencedirect_rss_fallback",
        "publisher_rss_lag_crossref_fallback",
        "official_order_unverified",
    }
    return [
        flag for flag in issue.get("quality", {}).get("flags", [])
        if flag not in content_only
    ]


def order_verification_status(issue: dict[str, Any]) -> str:
    """Return the reader-facing issue-order verification level."""

    flags = set(issue.get("quality", {}).get("flags", []))
    if {
        "official_order_unverified",
        "crossref_provisional_roster",
    } & flags:
        return "pending_official"
    return "official_verified"


def is_publishable_snapshot(issue: dict[str, Any]) -> bool:
    article_count = len(issue.get("articles", []))
    research_count = issue.get("research_article_count", 0)
    quality = issue.get("quality", {})
    abstracts_complete = all(
        abstract_is_complete(article) for article in issue.get("articles", [])
    )
    return (
        research_count > 0
        and article_count == research_count
        and issue.get("expected_article_count") == research_count
        and bool(quality.get("roster_match"))
        and bool(quality.get("order_preserved"))
        and quality.get("doi_complete") == research_count
        and quality.get("authors_complete") == research_count
        and abstracts_complete
        and quality.get("duplicate_count") == 0
        and not any(
            flag.startswith("collector_error:")
            for flag in quality.get("flags", [])
        )
    )


def enrich_detected_issue(
    config: dict[str, Any],
    issue: dict[str, Any],
    *,
    re_enrich_elsevier: bool = False,
) -> dict[str, Any]:
    """Retry only unfinished content for an already trusted issue roster."""

    if config.get("collector") != "elsevier":
        return issue
    from collectors.metadata_fallback import fetch_sciencedirect_rss_issue

    rss_url = config.get("rss_url") or (
        "https://rss.sciencedirect.com/publication/science/"
        f"{str(config['issn']).replace('-', '')}"
    )
    repec_match = re.search(
        r"/s/([^/]+/[^/.]+)\.html",
        str(config.get("repec_series_url", "")),
    )
    refreshed = fetch_sciencedirect_rss_issue(
        journal_id=config["id"],
        journal_name=config["name"],
        issn=str(config["issn"]),
        current_issue_url=config["current_issue_url"],
        issue_url_template=config["issue_url_template"],
        rss_url=rss_url,
        repec_series_code=repec_match.group(1) if repec_match else "",
        lead_months=int(config.get("publication_lead_months", 1)),
        existing_issue=issue,
        force_elsevier=re_enrich_elsevier,
    )
    return refreshed or issue


def preserve_existing_content(
    issue: dict[str, Any],
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep recovered content only while its English source remains unchanged."""

    if not existing or existing.get("issue_id") != issue.get("issue_id"):
        return issue
    existing_by_doi = {
        str(article.get("doi", "")).strip().lower(): article
        for article in existing.get("articles", [])
        if str(article.get("doi", "")).strip()
    }
    existing_by_title = {
        re.sub(r"[^a-z0-9]+", " ", str(article.get("title_en", "")).casefold()).strip(): article
        for article in existing.get("articles", [])
        if str(article.get("title_en", "")).strip()
    }
    for article in issue.get("articles", []):
        article["abstract_en"] = clean_abstract_label(article.get("abstract_en"))
        article["abstract_cn"] = clean_abstract_label(article.get("abstract_cn"))
        doi = str(article.get("doi", "")).strip().lower()
        title_key = re.sub(r"[^a-z0-9]+", " ", str(article.get("title_en", "")).casefold()).strip()
        old = existing_by_doi.get(doi) or existing_by_title.get(title_key)
        if not old:
            continue
        old_abstract_en = clean_abstract_label(old.get("abstract_en"))
        if not article.get("abstract_en") and old_abstract_en:
            article["abstract_en"] = old_abstract_en
        if not article.get("authors") and old.get("authors"):
            article["authors"] = list(old["authors"])

        source_matches = (
            str(article.get("title_en", "")).strip()
            == str(old.get("title_en", "")).strip()
            and str(article.get("abstract_en", "")).strip()
            == old_abstract_en.strip()
        )
        if source_matches:
            for field in ("title_cn", "abstract_cn"):
                if not article.get(field) and old.get(field):
                    article[field] = old[field]
            if old.get("translation", {}).get("status") == "complete":
                article["translation"] = dict(old["translation"])
        elif (
            article.get("title_cn") == old.get("title_cn")
            or article.get("abstract_cn")
            == clean_abstract_label(old.get("abstract_cn"))
        ):
            _clear_stale_translation(article)

        old_sources = old.get("sources", {})
        sources = article.setdefault("sources", {})
        for name, value in old_sources.items():
            if value and not sources.get(name):
                sources[name] = value
        flags = set(article.get("quality_flags", []))
        for field, flag in (
            ("title_cn", "title_cn_missing"),
            ("abstract_en", "abstract_en_missing"),
            ("abstract_cn", "abstract_cn_missing"),
        ):
            if article.get(field):
                flags.discard(flag)
        article["quality_flags"] = list(flags)

    quality = issue.get("quality", {})
    quality["authors_complete"] = sum(bool(article.get("authors")) for article in issue.get("articles", []))
    quality["abstract_en_complete"] = sum(
        abstract_is_complete(article) for article in issue.get("articles", [])
    )
    flags = [flag for flag in quality.get("flags", []) if flag != "abstract_en_incomplete"]
    if quality["abstract_en_complete"] != int(issue.get("research_article_count", 0)):
        flags.append("abstract_en_incomplete")
    quality["flags"] = list(dict.fromkeys(flags))
    # Provenance guard: a previously verified official roster must not be
    # downgraded back to the RSS fallback state while its article set is
    # unchanged. This keeps browser-verified Elsevier issues ready across
    # scheduled re-collections.
    existing_quality = (
        existing.get("quality", {}) if isinstance(existing, dict) else {}
    )
    verified_marker = existing_quality.get("browser_order_verification", {})
    official_verified = (
        existing_quality.get("roster_authority") == "official-issue-page"
        or (
            isinstance(verified_marker, dict)
            and bool(verified_marker.get("pii_sequence_matched"))
        )
        or bool(existing_quality.get("browser_capture"))
    )
    if official_verified:
        existing_dois = {
            str(article.get("doi", "")).strip().lower()
            for article in existing.get("articles", [])
            if str(article.get("doi", "")).strip()
        }
        new_dois = {
            str(article.get("doi", "")).strip().lower()
            for article in issue.get("articles", [])
            if str(article.get("doi", "")).strip()
        }
        if existing_dois and existing_dois == new_dois:
            downgrade_flags = {
                "publisher_html_blocked_sciencedirect_rss_fallback",
                "publisher_rss_reverse_order_normalized",
                "official_order_unverified",
                "elsevier_insttoken_required",
            }
            quality["flags"] = [
                flag
                for flag in quality.get("flags", [])
                if flag not in downgrade_flags
            ]
            for key in (
                "roster_authority",
                "roster_transport",
                "order_verification",
            ):
                if existing_quality.get(key):
                    quality[key] = existing_quality[key]
            for key in (
                "browser_order_verification",
                "browser_authorized_abstracts",
                "browser_capture",
            ):
                if existing_quality.get(key) and not quality.get(key):
                    quality[key] = existing_quality[key]
            if existing_quality.get("excluded_items") and not quality.get(
                "excluded_items"
            ):
                quality["excluded_items"] = list(existing_quality["excluded_items"])
                quality["excluded_item_count"] = len(
                    existing_quality["excluded_items"]
                )
    return issue


def merge_issue_audit_metadata(
    previous: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Refresh non-content counts without replacing the last ready articles."""

    if previous.get("issue_id") != candidate.get("issue_id"):
        return previous
    content_counts = candidate.get("content_counts")
    candidate_quality = candidate.get("quality", {})
    if content_counts:
        previous["content_counts"] = dict(content_counts)
    previous_quality = previous.setdefault("quality", {})
    for field in ("content_counts", "excluded_items", "official_item_count"):
        if field in candidate_quality:
            value = candidate_quality[field]
            previous_quality[field] = (
                [dict(item) for item in value]
                if isinstance(value, list)
                else dict(value)
                if isinstance(value, dict)
                else value
            )
    return previous

def collect_one(
    key: str,
    config: dict[str, Any],
    *,
    translate: bool,
    expected_volume: str = "",
    expected_issue: str = "",
    enrich_detected: bool = False,
    translation_provider_state: dict[str, str] | None = None,
    re_enrich_elsevier: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    report: dict[str, Any] = {
        "journal": key,
        "journal_id": config["id"],
        "source_url": config["current_issue_url"],
        "started_at": now_iso(),
    }
    target = public_issue_path(config["id"])
    previous = read_json(target)
    previous_detected = read_json(detected_issue_path(config["id"]))
    detected_before: dict[str, Any] | None = None
    try:
        if enrich_detected:
            detected = read_json(detected_issue_path(config["id"]))
            if not detected or detected.get("publication_state") == "ready":
                report.update(
                    {
                        "result": "self_heal_skipped",
                        "reason": "no incomplete detected issue",
                        "finished_at": now_iso(),
                    }
                )
                return previous, report
            detected_before = detected
            detected_count = int(detected.get("research_article_count", 0))
            abstract_count = int(
                detected.get("quality", {}).get("abstract_en_complete", 0)
            )
            if (
                detected_count > 0
                and abstract_count == detected_count
                and not re_enrich_elsevier
            ):
                # Translation-only recovery must not revisit publisher pages or
                # entitlement-gated abstract APIs.
                issue = detected
                report["transport"] = "translation-only"
            else:
                issue = enrich_detected_issue(
                    config, detected, re_enrich_elsevier=re_enrich_elsevier
                )
                report["transport"] = "detected-self-heal"
        else:
            primary_error = ""
            try:
                issue = collector_for(config)()
                if not is_detected_snapshot(issue):
                    raise ValueError(
                        "primary collector failed roster gate: "
                        + ", ".join(
                            structural_flags(issue) or ["untrusted official roster"]
                        )
                    )
            except Exception as error:
                primary_error = f"{type(error).__name__}: {error}"
                fallback = fallback_collector_for(config)
                if fallback is None:
                    raise
                issue = fallback()
                report["primary_error"] = primary_error
                report["transport"] = "metadata_fallback"
        issue = preserve_existing_content(issue, detected_before or previous_detected)
        issue = preserve_existing_content(issue, previous)
        # A publisher's current endpoint can lag RSS or a previously detected
        # official roster. Never replace a newer trusted snapshot with it.
        if (
            previous_detected
            and is_detected_snapshot(previous_detected)
            and issue.get("journal_id") == previous_detected.get("journal_id")
            and issue.get("issue_id") != previous_detected.get("issue_id")
            and issue_is_newer(previous_detected, issue)
        ):
            issue = previous_detected
            report["result_detail"] = "preserved_newer_detected_issue"
        if "crossref_provisional_roster" in issue.get("quality", {}).get(
            "flags", []
        ):
            # Crossref can describe a candidate, but it cannot replace the
            # publisher-confirmed roster. Keep the last-known-good public issue
            # while allowing that older snapshot to remain visible in indexes.
            raise ValueError(
                "provisional Crossref roster requires official confirmation"
            )
        if expected_volume and str(issue.get("volume", "")) != expected_volume:
            raise SourceLagError(
                f"detected volume {expected_volume}, but the deep collector "
                f"still returns volume {issue.get('volume', '')}"
            )
        if expected_issue and str(issue.get("issue", "")) != expected_issue:
            raise SourceLagError(
                f"detected issue {expected_issue}, but the deep collector "
                f"still returns issue {issue.get('issue', '')}"
            )
        issue = apply_translation_cache(issue)
        issue = normalize_issue_content(issue)
        validate_issue(issue)
        if is_detected_snapshot(issue):
            write_detected_snapshot(issue)
        translation_report: dict[str, Any] | None = None
        if translate:
            translation_report = translate_missing(
                issue,
                TRANSLATION_CACHE / f"{config['id']}.json",
                provider_state=translation_provider_state,
            )
            issue = apply_translation_cache(issue)
        issue = normalize_issue_content(issue)
        validate_issue(issue)
        if is_detected_snapshot(issue):
            write_detected_snapshot(issue)
        if not is_publishable_snapshot(issue):
            if enrich_detected:
                quality = issue.get("quality", {})
                abstracts = int(quality.get("abstract_en_complete", 0))
                translations = int(quality.get("translation_complete", 0))
                prior_quality = (detected_before or {}).get("quality", {})
                progressed = (
                    abstracts > int(prior_quality.get("abstract_en_complete", 0))
                    or translations
                    > int(prior_quality.get("translation_complete", 0))
                )
                if not progressed and detected_before is not None:
                    write_json(
                        detected_issue_path(config["id"]),
                        detected_before,
                    )
                report.update(
                    {
                        "result": (
                            "detected_progress"
                            if progressed
                            else "self_heal_no_change"
                        ),
                        "issue_id": issue.get("issue_id", ""),
                        "articles": issue.get("research_article_count", 0),
                        "abstracts": abstracts,
                        "translations": translations,
                        "finished_at": now_iso(),
                    }
                )
                return previous, report
            raise ValueError(
                "collector result failed the publication gate: "
                + ", ".join(structural_flags(issue) or ["empty official roster"])
            )
        translated = int(issue["quality"].get("translation_complete", 0))
        total = int(issue["research_article_count"])
        if translated != total:
            if previous and previous.get("issue_id") == issue.get("issue_id"):
                previous = merge_issue_audit_metadata(previous, issue)
            if enrich_detected:
                abstracts = int(
                    issue["quality"].get("abstract_en_complete", 0)
                )
                prior_quality = (detected_before or {}).get("quality", {})
                progressed = (
                    abstracts > int(prior_quality.get("abstract_en_complete", 0))
                    or translated
                    > int(prior_quality.get("translation_complete", 0))
                )
                if not progressed and detected_before is not None:
                    write_json(
                        detected_issue_path(config["id"]),
                        detected_before,
                    )
                report.update(
                    {
                        "result": (
                            "detected_progress"
                            if progressed
                            else "self_heal_no_change"
                        ),
                        "issue_id": issue.get("issue_id", ""),
                        "articles": total,
                        "abstracts": abstracts,
                        "translations": translated,
                        "finished_at": now_iso(),
                    }
                )
                return previous, report
            raise ValueError(
                f"translation incomplete: {translated}/{total}; "
                "preserving the last complete public issue"
            )
        if previous and previous.get("issue_id") != issue.get("issue_id"):
            archive_issue(previous)
        issue["publication_state"] = "ready"
        write_json(target, issue)
        write_detected_snapshot(issue)
        readback = read_json(target)
        if readback is None or readback.get("issue_id") != issue["issue_id"]:
            raise RuntimeError("public issue write-back verification failed")
        archive_issue(issue)
        report.update(
            {
                "result": "updated",
                "issue_id": issue["issue_id"],
                "articles": issue["research_article_count"],
                "content_status": (
                    "complete"
                    if issue["quality"]["translation_complete"]
                    == issue["research_article_count"]
                    else "translation_incomplete"
                ),
                "data_status": (
                    "healthy" if not structural_flags(issue) else "fallback"
                ),
                "translation": translation_report,
                "finished_at": now_iso(),
            }
        )
        return issue, report
    except Exception as error:
        report.update(
            {
                "result": "preserved_previous" if previous else "failed",
                "error": f"{type(error).__name__}: {error}",
                "finished_at": now_iso(),
            }
        )
        return previous, report


def load_available_issues(
    journal_configs: dict[str, dict[str, Any]],
    refreshed: dict[str, dict[str, Any] | None],
) -> dict[str, dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {}
    for key, config in journal_configs.items():
        if not config.get("enabled"):
            continue
        issue = refreshed.get(key)
        if issue is None:
            issue = read_json(public_issue_path(config["id"]))
        if issue:
            try:
                issue = normalize_issue_content(issue)
                validate_issue(issue)
            except ValueError:
                continue
            write_json(public_issue_path(config["id"]), issue)
            available[key] = issue
    return available


def update_indexes(
    journal_configs: dict[str, dict[str, Any]],
    issues: dict[str, dict[str, Any]],
) -> None:
    updated_at = now_iso()
    collection_config = yaml.safe_load(
        (ROOT / "config" / "collections.yml").read_text(encoding="utf-8")
    )["collections"]
    journal_entries: dict[str, dict[str, Any]] = {}
    checks: dict[str, Any] = {}
    enabled_count = 0
    usable_count = 0
    translated_articles = 0
    total_articles = 0

    for key, config in journal_configs.items():
        if not config.get("enabled"):
            continue
        enabled_count += 1
        issue = issues.get(key)
        entry: dict[str, Any] = {
            "journal_id": config["id"],
            "short_name": config["short_name"],
            "name": config["name"],
            "name_cn": config.get("name_cn", ""),
            "field": config.get("field", "general"),
            "tier": config.get("tier", "S"),
            "collections": config.get("collections", []),
            "status": "unavailable",
        }
        detected = read_json(detected_issue_path(config["id"]))
        if detected and is_detected_snapshot(detected):
            detected_total = int(detected.get("research_article_count", 0))
            detected_abstracts = int(
                detected.get("quality", {}).get("abstract_en_complete", 0)
            )
            detected_quality = detected.get("quality", {})
            detected_translations = int(
                detected_quality.get("translation_complete", 0)
            )
            detected_counts = detected.get("content_counts") or detected_quality.get(
                "content_counts", {}
            )
            entry.update(
                {
                    "latest_detected_issue_id": detected.get("issue_id", ""),
                    "latest_detected_issue_url": (
                        f"/journals/api/v1/journals/{config['id']}"
                        "/issues/detected.json"
                    ),
                    "latest_detected_issue_label": canonical_issue_label(
                        detected.get("volume"),
                        detected.get("issue"),
                        detected.get("issue_label"),
                    ),
                    "latest_detected_publication_date": detected.get(
                        "publication_date", ""
                    ),
                    "latest_detected_article_count": detected_total,
                    "latest_detected_abstracts_complete": detected_abstracts,
                    "latest_detected_translations_complete": detected_translations,
                    "latest_detected_content_counts": detected_counts,
                    "latest_detected_roster_authority": detected_quality.get(
                        "roster_authority", "official-issue-page"
                    ),
                    "latest_detected_roster_transport": detected_quality.get(
                        "roster_transport", "official-issue-page"
                    ),
                    "latest_detected_order_verification": order_verification_status(
                        detected
                    ),
                    "update_state": (
                        "ready"
                        if detected.get("publication_state") == "ready"
                        else "enriching"
                    ),
                }
            )
        if issue and is_publishable_snapshot(issue):
            usable_count += 1
            translated = issue["quality"]["translation_complete"]
            total = issue["research_article_count"]
            translated_articles += translated
            total_articles += total
            entry.update(
                {
                    "status": issue["status"],
                    "data_status": (
                        "healthy" if not structural_flags(issue) else "needs_attention"
                    ),
                    "content_status": (
                        "complete" if translated == total else "translation_incomplete"
                    ),
                    "latest_issue_id": issue["issue_id"],
                    "latest_ready_issue_id": issue["issue_id"],
                    "latest_issue_url": (
                        f"/journals/api/v1/journals/{config['id']}"
                        "/issues/current.json"
                    ),
                    "latest_ready_issue_url": (
                        f"/journals/api/v1/journals/{config['id']}"
                        "/issues/current.json"
                    ),
                    "latest_issue_label": canonical_issue_label(
                        issue.get("volume"), issue.get("issue"), issue.get("issue_label")
                    ),
                    "publication_date": issue.get("publication_date", ""),
                    "article_count": total,
                    "translation_complete": translated,
                    "content_counts": issue.get("content_counts")
                    or issue.get("quality", {}).get("content_counts", {}),
                    "roster_authority": issue.get("quality", {}).get(
                        "roster_authority", "official-issue-page"
                    ),
                    "roster_transport": issue.get("quality", {}).get(
                        "roster_transport", "official-issue-page"
                    ),
                    "order_verification": order_verification_status(issue),
                }
            )
            if not entry.get("latest_detected_issue_url"):
                entry.update(
                    {
                        "latest_detected_issue_id": issue["issue_id"],
                        "latest_detected_issue_url": entry["latest_issue_url"],
                        "latest_detected_issue_label": entry["latest_issue_label"],
                        "latest_detected_publication_date": issue.get(
                            "publication_date", ""
                        ),
                        "latest_detected_article_count": total,
                        "latest_detected_abstracts_complete": int(
                            issue["quality"].get("abstract_en_complete", 0)
                        ),
                        "latest_detected_translations_complete": translated,
                        "latest_detected_content_counts": entry.get("content_counts", {}),
                        "latest_detected_roster_authority": entry.get(
                            "roster_authority", "official-issue-page"
                        ),
                        "latest_detected_roster_transport": entry.get(
                            "roster_transport", "official-issue-page"
                        ),
                        "latest_detected_order_verification": entry.get(
                            "order_verification", "pending_official"
                        ),
                        "update_state": "ready",
                    }
                )
            checks[f"{config['id']}_roster_match"] = issue["quality"]["roster_match"]
            checks[f"{config['id']}_order_preserved"] = issue["quality"]["order_preserved"]
            checks[f"{config['id']}_primary_transport"] = not bool(
                structural_flags(issue)
            )
        else:
            checks[f"{config['id']}_available"] = False
        journal_entries[key] = entry
        if issue:
            archive_issue(issue)
            write_archive_index(
                config["id"],
                config["name"],
                updated_at=updated_at,
            )

    data_healthy = usable_count == enabled_count and all(
        value is True for value in checks.values()
    )
    content_complete = total_articles > 0 and translated_articles == total_articles
    collection_indexes: list[dict[str, Any]] = []
    for collection_id, definition in collection_config.items():
        keys = [
            key
            for key in definition.get("journals", [])
            if key in journal_entries
        ]
        entries = [journal_entries[key] for key in keys]
        collection_usable = sum(
            bool(entry.get("latest_issue_url")) for entry in entries
        )
        collection_translated = sum(
            int(entry.get("translation_complete", 0)) for entry in entries
        )
        collection_articles = sum(int(entry.get("article_count", 0)) for entry in entries)
        collection_data_healthy = bool(entries) and collection_usable == len(entries) and all(
            entry.get("data_status") == "healthy" for entry in entries
        )
        collection_content_complete = (
            collection_articles > 0 and collection_translated == collection_articles
        )
        payload = {
            "schema_version": "1.0",
            "collection_id": collection_id,
            "title": definition["name"],
            "title_cn": definition.get("name_cn", ""),
            "updated_at": updated_at,
            "data_status": "healthy" if collection_data_healthy else "degraded",
            "content_status": "complete" if collection_content_complete else "translation_incomplete",
            "summary": {
                "configured_journals": len(entries),
                "available_journals": collection_usable,
                "articles": collection_articles,
                "translated_articles": collection_translated,
            },
            "journals": entries,
        }
        write_json(PUBLIC_API / "collections" / f"{collection_id}.json", payload)
        collection_indexes.append(
            {
                "id": collection_id,
                "title": definition["name"],
                "title_cn": definition.get("name_cn", ""),
                "url": f"/journals/api/v1/collections/{collection_id}.json",
            }
        )
    health = {
        "schema_version": "1.0",
        "updated_at": updated_at,
        "status": "healthy" if data_healthy else "degraded",
        "content_status": "complete" if content_complete else "translation_incomplete",
        "summary": {
            "enabled_journals": enabled_count,
            "available_journals": usable_count,
            "articles": total_articles,
            "translated_articles": translated_articles,
        },
        "checks": checks,
    }
    index = {
        "schema_version": "1.0",
        "updated_at": updated_at,
        "collections": collection_indexes,
    }
    manifest = {
        "project_id": "journals",
        "title": "Academic Door Journals",
        "description": "经济学期刊目录、双语摘要与 Academic Door Composer",
        "url": "https://academic-door.github.io/journals/",
        "updated_at": updated_at,
        "status": health["status"],
        "latest_title": f"期刊最新卷期 · {usable_count}/{enabled_count} 家期刊可用",
        "latest_url": "https://academic-door.github.io/journals/",
        "data_url": "https://academic-door.github.io/journals/api/v1/index.json",
        "feed_url": "https://academic-door.github.io/journals/feeds/all.xml",
    }
    write_json(PUBLIC_API / "health.json", health)
    write_json(PUBLIC_API / "index.json", index)
    write_json(ROOT / "public" / "project-manifest.json", manifest)
    write_search_indexes(
        journal_configs,
        issues,
        updated_at=updated_at,
    )

    for collection_id, definition in collection_config.items():
        readback = read_json(PUBLIC_API / "collections" / f"{collection_id}.json")
        expected = sum(key in journal_entries for key in definition.get("journals", []))
        if readback is None or len(readback.get("journals", [])) != expected:
            raise RuntimeError(f"{collection_id} collection write-back verification failed")


def main() -> int:
    config = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))
    journal_configs = config["journals"]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--journal",
        default="ALL",
        choices=["ALL", *journal_configs.keys()],
    )
    parser.add_argument(
        "--translate",
        action="store_true",
        help="Translate missing Chinese titles and abstracts through GitHub Models.",
    )
    parser.add_argument(
        "--expected-volume",
        default="",
        help="Preserve the previous snapshot until the collector reaches this volume.",
    )
    parser.add_argument(
        "--expected-issue",
        default="",
        help="Preserve the previous snapshot until the collector reaches this issue.",
    )
    parser.add_argument(
        "--enrich-detected",
        action="store_true",
        help="Retry missing abstracts and translations on detected issues only.",
    )
    parser.add_argument(
        "--re-enrich-elsevier",
        action="store_true",
        help="Force Elsevier API abstract lookups even when an abstract already "
        "exists (keeps roster and order untouched; never downgrades to a teaser).",
    )
    args = parser.parse_args()

    selected = [
        key
        for key, journal in journal_configs.items()
        if journal.get("enabled") and (args.journal == "ALL" or key == args.journal)
    ]
    refreshed: dict[str, dict[str, Any] | None] = {}
    reports: list[dict[str, Any]] = []
    translation_provider_state: dict[str, str] = {}
    for key in selected:
        issue, report = collect_one(
            key,
            journal_configs[key],
            translate=args.translate,
            expected_volume=args.expected_volume,
            expected_issue=args.expected_issue,
            enrich_detected=args.enrich_detected,
            translation_provider_state=translation_provider_state,
            re_enrich_elsevier=args.re_enrich_elsevier,
        )
        refreshed[key] = issue
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False))

    available = load_available_issues(journal_configs, refreshed)
    update_indexes(journal_configs, available)
    final_report = {
        "updated_at": now_iso(),
        "requested": args.journal,
        "translate": args.translate,
        "enrich_detected": args.enrich_detected,
        "re_enrich_elsevier": args.re_enrich_elsevier,
        "results": reports,
        "translation_provider_state": translation_provider_state,
        "available_journals": sorted(available),
    }
    write_json(UPDATE_REPORT, final_report)
    return 1 if any(item["result"] == "failed" for item in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
