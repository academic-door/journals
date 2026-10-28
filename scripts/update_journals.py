from __future__ import annotations

import copy
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
from collectors.metadata_fallback import _strip_abstract_footnotes
from collectors.article_types import (
    abstract_is_complete,
    canonical_issue_label,
    has_official_no_abstract_exception,
    normalize_issue_taxonomy,
    normalize_no_abstract_comment,
    translation_is_complete,
)
from scripts.china_relevance import annotate_issue, classify_china_relevance
from scripts.b8_rights_qualified import MODE as B8_METADATA_MODE, validate_public_projection
from scripts.freshness_announcements import announcement_entry_fields
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
MONITOR_STATE_PATH = ROOT / "data" / "monitoring" / "state.json"
COMMENT_TITLE_OVERRIDES = {
    "10.1086/740225": "国家起源：土地生产率还是可攫取性？——评论",
}
UPDATE_REPORT = ROOT / "output" / "journal-update-report.json"
ARCHIVE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
JIE_163_ARCHIVE_CORRECTION_EVIDENCE = (
    ROOT / "data" / "provenance" / "official-rosters" / "jie" / "jie-163-c.json"
)
JIE_163_OFFICIAL_RESEARCH_DOIS = (
    "10.1016/j.jinteco.2025.104203",
    "10.1016/j.jinteco.2025.104207",
    "10.1016/j.jinteco.2025.104204",
    "10.1016/j.jinteco.2026.104219",
    "10.1016/j.jinteco.2026.104225",
    "10.1016/j.jinteco.2026.104231",
    "10.1016/j.jinteco.2026.104230",
    "10.1016/j.jinteco.2026.104290",
)
JIE_163_LEGACY_ARCHIVE_DOIS = (
    "10.1016/j.jinteco.2025.104203",
    "10.1016/j.jinteco.2025.104204",
    "10.1016/j.jinteco.2025.104207",
    "10.1016/j.jinteco.2026.104219",
    "10.1016/j.jinteco.2026.104225",
    "10.1016/j.jinteco.2026.104230",
    "10.1016/j.jinteco.2026.104231",
    "10.1016/j.jinteco.2026.104290",
)
JIE_163_OFFICIAL_EXCLUDED_DOIS = (
    "10.1016/s0022-1996(26)00094-2",
    "10.1016/j.jinteco.2026.104295",
)
JIE_163_LEGACY_EXCLUDED_DOIS = (
    "10.1016/j.jinteco.2026.104295",
)


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


def abstract_without_body_is_allowed(article: dict[str, Any]) -> bool:
    return comment_without_abstract(article) or has_official_no_abstract_exception(article)


def issue_content_status(issue: dict[str, Any]) -> str:
    """Classify content completeness independently from source authority."""

    count = int(issue.get("research_article_count", 0))
    if issue.get("public_mode") == B8_METADATA_MODE:
        return "metadata_complete" if is_publishable_snapshot(issue) else "blocked"
    if not is_publishable_snapshot(issue):
        return "blocked"
    if count > 0 and int(issue.get("quality", {}).get("translation_complete", 0)) == count:
        return "complete"
    return "translation_partial"


def issue_source_status(issue: dict[str, Any]) -> str:
    """Return the strongest source authority supported by issue evidence.

    Crossref is useful for discovery and metadata enrichment, but it is never
    authoritative for issue membership or order.  Publisher TOC/RSS and
    publisher-supplied serial feeds are accepted as publisher verification;
    an official issue page (including an authorised browser capture) is the
    strongest level.
    """

    quality = issue.get("quality", {})
    flags = {str(flag) for flag in quality.get("flags", [])}
    authority = str(quality.get("roster_authority", "")).strip().casefold()
    transport = str(quality.get("roster_transport", "")).strip().casefold()
    source_url = str(issue.get("source_url", "")).strip().casefold()

    if (
        "crossref_provisional_roster" in flags
        or "crossref-provisional" in authority
        or authority == "crossref"
        or transport == "crossref"
        or transport.startswith("crossref-")
    ):
        return "source_pending"

    browser_marker = quality.get("browser_order_verification", {})
    browser_verified = (
        isinstance(browser_marker, dict)
        and bool(browser_marker.get("pii_sequence_matched"))
    ) or bool(quality.get("browser_capture"))
    if (
        authority == "official-issue-page"
        or browser_verified
        or "official-issue-page" in transport
    ):
        return "official_verified"

    publisher_authorities = {
        "publisher-rss",
        "repec-publisher-supplied",
        "elsevier-search-api",
        "repec-serial-page",
    }
    if authority in publisher_authorities or transport in {
        "publisher-rss",
        "sciencedirect-rss",
        "repec-serial-page",
        "elsevier-search-api",
    }:
        return "publisher_verified"

    # Older AEA/OUP/Wiley snapshots predate explicit roster provenance.  A
    # clean snapshot whose source itself is an official publisher issue page
    # retains its verifiable authority during the 1.1 state migration.
    official_hosts = (
        "aeaweb.org/",
        "academic.oup.com/",
        "journals.uchicago.edu/",
        "onlinelibrary.wiley.com/",
    )
    if not authority and not transport and any(host in source_url for host in official_hosts):
        return "official_verified"
    return "source_pending"


def issue_publication_state(issue: dict[str, Any]) -> str:
    """Combine content and source gates into the public publishing state."""

    content_status = issue_content_status(issue)
    source_status = issue_source_status(issue)
    if content_status in {"complete", "metadata_complete"} and source_status in {
        "official_verified",
        "publisher_verified",
    }:
        return "ready"
    if content_status == "complete":
        return "source_pending"
    if content_status == "translation_partial":
        return "translation_partial"
    return "blocked"


def stamp_issue_readiness(issue: dict[str, Any]) -> dict[str, Any]:
    """Add the additive 1.0 issue readiness contract in-place."""

    issue["content_status"] = issue_content_status(issue)
    issue["source_status"] = issue_source_status(issue)
    issue["publication_state"] = issue_publication_state(issue)
    return issue


def article_type_overrides() -> dict[str, str]:
    try:
        payload = json.loads(ARTICLE_TYPE_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def normalize_issue_content(issue: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic taxonomy, quality counts, and reader cleanup."""

    for article in issue.get("articles", []):
        article["abstract_en"] = _strip_abstract_footnotes(
            clean_abstract_label(article.get("abstract_en"))
        )
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
    return stamp_issue_readiness(annotate_issue(issue))


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
        # Only metadata: a rejected cache entry should be diagnosable
        # without logging source texts, Chinese translations, or secrets.
        if not translation_is_complete(article):
            if not translated:
                print(
                    f"[translation-cache] missing doi={article.get('doi', '')} "
                    f"cache={cache_path.name} source_sha256={_source_hash(article)}",
                    flush=True,
                )
            elif not _translation_payload_is_current(article, translated):
                reason = (
                    "source_hash_mismatch"
                    if cache_hash and cache_hash != _source_hash(article)
                    else "semantic_or_structural_validation"
                )
                print(
                    f"[translation-cache] rejected doi={article.get('doi', '')} "
                    f"reason={reason} source_sha256={_source_hash(article)} "
                    f"cached_sha256={cache_hash or 'unset'} "
                    f"english_chars={len(str(article.get('abstract_en', '')))}",
                    flush=True,
                )
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
        elif abstract_without_body_is_allowed(article):
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
        normalize_no_abstract_comment(article)
        if article["title_cn"] and article["abstract_cn"]:
            article["translation"]["status"] = "complete"
        elif abstract_without_body_is_allowed(article) and article["title_cn"]:
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


def collector_for(
    config: dict[str, Any],
    *,
    expected_volume: str = "",
    expected_issue: str = "",
) -> Callable[[], dict[str, Any]]:
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
            expected_volume=expected_volume,
            expected_issue=expected_issue,
        )
    if collector == "springer":
        from collectors.springer import fetch_current_issue as fetch_springer

        return lambda: fetch_springer(
            config,
            expected_volume=expected_volume,
            expected_issue=expected_issue,
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


def fallback_collector_for(
    config: dict[str, Any],
    *,
    expected_volume: str = "",
    expected_issue: str = "",
) -> Callable[[], dict[str, Any]] | None:
    fallback = config.get("fallback", "")
    collectors: list[Callable[[], dict[str, Any]]] = []
    if (
        config.get("collector") == "chicago"
        and fallback == "crossref-repec"
        and config.get("repec_exact_issue_fallback") is True
        and expected_volume
        and expected_issue
        and config.get("repec_series_code")
    ):
        # Only an explicitly identified candidate may use a publisher-supplied
        # RePEc issue section as a roster fallback when official Chicago HTML
        # is 403. Never turn a Crossref guess or a future schedule into READY.
        from collectors.metadata_fallback import fetch_repec_history_issue

        collectors.append(
            lambda: fetch_repec_history_issue(
                journal_id=str(config["id"]),
                journal_name=str(config["name"]),
                issn=str(config["issn"]),
                volume=expected_volume,
                issue=expected_issue,
                repec_series_code=str(config["repec_series_code"]),
            )
        )
    if config.get("announcement_source") == "econometric_society_volume":
        from collectors.econometric_society import fetch_latest_econometrica_issue

        collectors.append(
            lambda: fetch_latest_econometrica_issue(
                source_url=str(config.get("announcement_url", "")),
                journal_id=config["id"],
                journal_name=config["name"],
                issn=str(config["issn"]),
                current_issue_url=config["current_issue_url"],
                repec_series_code=str(config.get("repec_series_code", "")),
            )
        )
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
    seasonal = re.fullmatch(
        r"(Spring|Summer|Fall|Autumn|Winter)\s+(20\d{2})",
        value,
        flags=re.IGNORECASE,
    )
    if seasonal:
        month = {
            "spring": 3,
            "summer": 6,
            "fall": 9,
            "autumn": 9,
            "winter": 12,
        }[seasonal.group(1).casefold()]
        return int(seasonal.group(2)), month
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


def select_display_issue(
    detected: dict[str, Any] | None,
    ready: dict[str, Any] | None,
) -> str:
    """Choose the reader-facing snapshot without allowing detected regressions.

    Ready is canonical for the same issue identity. A detected snapshot may lead
    only when it is genuinely newer than the last ready issue. Raw detected
    data remains available for diagnostics and enrichment.
    """

    if detected is None and ready is None:
        return ""
    if ready is None:
        return "detected"
    if detected is None:
        return "ready"
    if str(detected.get("issue_id", "")) == str(ready.get("issue_id", "")):
        return "ready"
    return "detected" if issue_is_newer(detected, ready) else "ready"


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
    stamp_issue_readiness(issue)
    target = detected_issue_path(str(issue["journal_id"]))
    write_json(target, issue)
    readback = read_json(target)
    if readback is None or readback.get("issue_id") != issue.get("issue_id"):
        raise RuntimeError("detected issue write-back verification failed")
    return target


def is_archivable_snapshot(issue: dict[str, Any]) -> bool:
    if issue.get("public_mode") == B8_METADATA_MODE:
        return is_publishable_snapshot(issue)
    quality = issue.get("quality", {})
    count = int(issue.get("research_article_count", 0))
    return (
        is_publishable_snapshot(issue)
        and count > 0
        and int(quality.get("translation_complete", 0)) == count
    )


def _doi_sequence(issue: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(article.get("doi", "")).strip().casefold()
        for article in issue.get("articles", [])
    )


def _excluded_doi_sequence(issue: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(item.get("doi", "")).strip().casefold()
        for item in issue.get("quality", {}).get("excluded_items", [])
    )


def _jie_163_candidate_matches_pinned_roster(
    candidate: dict[str, Any],
) -> bool:
    if (
        candidate.get("journal_id") != "jie"
        or candidate.get("issue_id") != "jie-163-c"
        or str(candidate.get("volume")) != "163"
        or str(candidate.get("issue", "")).casefold() != "c"
        or issue_publication_state(candidate) != "ready"
        or issue_source_status(candidate) != "official_verified"
    ):
        return False

    try:
        from scripts.import_official_roster_evidence import validate_evidence

        evidence = read_json(JIE_163_ARCHIVE_CORRECTION_EVIDENCE)
        if evidence is None:
            return False
        validate_evidence(evidence)
        captured_at = datetime.fromisoformat(
            str(evidence["captured_at"]).replace("Z", "+00:00")
        )
        retrieved_at = datetime.fromisoformat(
            str(candidate["retrieved_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError, OSError):
        return False

    if (
        evidence.get("finalized") is not True
        or evidence.get("capture_mode") != "official-roster-evidence"
        or evidence.get("journal_id") != "jie"
        or evidence.get("issue_id") != "jie-163-c"
        or evidence.get("official_url")
        != "https://www.sciencedirect.com/journal/journal-of-international-economics/vol/163/suppl/C"
        or evidence.get("method") != "official-page-read"
        or captured_at.tzinfo is None
        or captured_at > datetime.now(timezone.utc)
        or retrieved_at.tzinfo is None
        or retrieved_at < captured_at
    ):
        return False

    official_items = sorted(
        evidence.get("items", []),
        key=lambda item: int(item.get("source_sequence", 0)),
    )
    official_excluded = sorted(
        evidence.get("excluded_items", []),
        key=lambda item: int(item.get("source_sequence", 0)),
    )
    expected_research = tuple(
        doi.casefold() for doi in JIE_163_OFFICIAL_RESEARCH_DOIS
    )
    expected_excluded = tuple(
        doi.casefold() for doi in JIE_163_OFFICIAL_EXCLUDED_DOIS
    )
    if (
        tuple(str(item.get("doi", "")).casefold() for item in official_items)
        != expected_research
        or tuple(
            int(item.get("source_sequence", 0)) for item in official_items
        )
        != tuple(range(3, 11))
        or any(
            item.get("publisher_article_type") != "research article"
            for item in official_items
        )
        or tuple(
            str(item.get("doi", "")).casefold() for item in official_excluded
        )
        != expected_excluded
        or tuple(
            int(item.get("source_sequence", 0)) for item in official_excluded
        )
        != (1, 2)
        or any(
            item.get("article_type") != "editorial"
            for item in official_excluded
        )
        or any(
            item.get("reason") != "editorial_material"
            for item in official_excluded
        )
    ):
        return False

    quality = candidate.get("quality", {})
    roster_evidence = quality.get("official_roster_evidence", {})
    if (
        quality.get("roster_authority") != "official-issue-page"
        or quality.get("roster_transport") != "official-page-read"
        or quality.get("roster_match_scope") != "official-publisher-issue-full"
        or quality.get("official_item_count") != 10
        or quality.get("publishable_item_count") != 8
        or quality.get("excluded_item_count") != 2
        or quality.get("duplicate_count") != 0
        or quality.get("translation_complete") != 8
        or quality.get("content_counts", {}).get("research_articles") != 8
        or quality.get("content_counts", {}).get("editorial_material") != 2
        or roster_evidence.get("captured_at") != evidence.get("captured_at")
        or roster_evidence.get("official_url") != evidence.get("official_url")
        or roster_evidence.get("method") != evidence.get("method")
        or roster_evidence.get("item_count") != 8
        or roster_evidence.get("excluded_item_count") != 2
        or roster_evidence.get("privacy_fields_stored") is not False
        or quality.get("publisher_page_status") != "first-party-observed"
    ):
        return False

    articles = candidate.get("articles", [])
    if len(articles) != 8:
        return False
    for article, source in zip(articles, official_items, strict=True):
        pii_match = re.search(
            r"/pii/([A-Z0-9]+)",
            str(article.get("source_url", "")),
            re.IGNORECASE,
        )
        expected_source_id = str(source.get("source_id", "")).split(":", 1)[-1]
        if (
            str(article.get("doi", "")).casefold()
            != str(source.get("doi", "")).casefold()
            or article.get("sequence") != source.get("sequence")
            or article.get("source_sequence") != source.get("source_sequence")
            or article.get("article_type") != "research-article"
            or not pii_match
            or pii_match.group(1).upper() != expected_source_id.upper()
            or article.get("translation", {}).get("status") != "complete"
            or not str(article.get("title_cn", "")).strip()
            or not str(article.get("abstract_cn", "")).strip()
        ):
            return False

    excluded = sorted(
        quality.get("excluded_items", []),
        key=lambda item: int(item.get("source_sequence", 0)),
    )
    if len(excluded) != 2:
        return False
    excluded_fields = (
        "doi",
        "title_en",
        "source_sequence",
        "source_id",
        "article_type",
        "reason",
        "source_url",
    )
    for actual, source in zip(excluded, official_excluded, strict=True):
        if any(
            str(actual.get(field, "")).casefold()
            != str(source.get(field, "")).casefold()
            for field in excluded_fields
        ):
            return False
    return True


def _matches_jie_163_official_archive_correction(
    existing: dict[str, Any],
    candidate: dict[str, Any],
) -> str | None:
    """Classify the single stale JIE archive or its idempotent corrected state."""

    if (
        existing.get("journal_id") != "jie"
        or existing.get("issue_id") != "jie-163-c"
        or str(existing.get("volume")) != "163"
        or str(existing.get("issue", "")).casefold() != "c"
        or issue_publication_state(existing) != "ready"
        or not _jie_163_candidate_matches_pinned_roster(candidate)
    ):
        return None

    old_by_doi = {
        str(article.get("doi", "")).casefold(): article
        for article in existing.get("articles", [])
    }
    candidate_by_doi = {
        str(article.get("doi", "")).casefold(): article
        for article in candidate.get("articles", [])
    }
    for doi in JIE_163_OFFICIAL_RESEARCH_DOIS:
        old_article = old_by_doi.get(doi.casefold())
        new_article = candidate_by_doi.get(doi.casefold())
        if not old_article or not new_article or any(
            new_article.get(field) != old_article.get(field)
            for field in ("title_cn", "abstract_cn", "translation")
        ):
            return None

    legacy_archive = (
        issue_source_status(existing) == "publisher_verified"
        and existing.get("quality", {}).get("roster_authority")
        == "repec-publisher-supplied"
        and existing.get("quality", {}).get("roster_transport")
        == "repec-serial-page"
        and existing.get("quality", {}).get("roster_match_scope")
        == "repec-volume-items"
        and _doi_sequence(existing)
        == tuple(doi.casefold() for doi in JIE_163_LEGACY_ARCHIVE_DOIS)
        and _excluded_doi_sequence(existing)
        == tuple(doi.casefold() for doi in JIE_163_LEGACY_EXCLUDED_DOIS)
    )
    if legacy_archive:
        return "stale"

    candidate_evidence = candidate.get("quality", {}).get(
        "official_roster_evidence", {}
    )
    existing_quality = existing.get("quality", {})
    already_corrected = (
        issue_source_status(existing) == "official_verified"
        and existing_quality.get("roster_authority") == "official-issue-page"
        and existing_quality.get("roster_transport") == "official-page-read"
        and existing_quality.get("roster_match_scope")
        == "official-publisher-issue-full"
        and existing_quality.get("official_item_count") == 10
        and existing_quality.get("publishable_item_count") == 8
        and existing_quality.get("excluded_item_count") == 2
        and existing_quality.get("official_roster_evidence")
        == candidate_evidence
        and _doi_sequence(existing)
        == tuple(doi.casefold() for doi in JIE_163_OFFICIAL_RESEARCH_DOIS)
        and _excluded_doi_sequence(existing)
        == tuple(doi.casefold() for doi in JIE_163_OFFICIAL_EXCLUDED_DOIS)
    )
    return "current" if already_corrected else None


def _preserve_jie_163_archive_observation(existing: dict[str, Any], candidate: dict[str, Any]) -> None:
    """Carry forward original first-seen/observed-at values without inventing them."""

    for field in ("first_seen_at", "observed_at"):
        if field in existing:
            if candidate.get(field) not in (None, existing[field]):
                raise ValueError(f"JIE archive correction conflicts with existing {field}")
            candidate[field] = copy.deepcopy(existing[field])

    old_by_doi = {
        str(article.get("doi", "")).casefold(): article
        for article in existing.get("articles", [])
    }
    for article in candidate.get("articles", []):
        old_article = old_by_doi.get(str(article.get("doi", "")).casefold(), {})
        for field in ("first_seen_at", "observed_at"):
            if field in old_article:
                if article.get(field) not in (None, old_article[field]):
                    raise ValueError(
                        f"JIE archive correction conflicts with article {field}"
                    )
                article[field] = copy.deepcopy(old_article[field])


def archive_issue(
    issue: dict[str, Any],
    *,
    api_root: Path = PUBLIC_API,
    replace_non_ready: bool = False,
    correct_jie_163_ready_archive: bool = False,
) -> Path | None:
    """Save one validated issue snapshot.

    Ready archives remain immutable by default. The sole bounded exception is
    an explicitly enabled correction of the known stale JIE 163 archive after
    the candidate is matched to its pinned first-party 8+2 roster. Provisional
    archives retain their existing promotion path.
    """

    journal_id = str(issue.get("journal_id", ""))
    issue_id = str(issue.get("issue_id", ""))
    if (
        not ARCHIVE_ID_PATTERN.fullmatch(journal_id)
        or not ARCHIVE_ID_PATTERN.fullmatch(issue_id)
        or not is_archivable_snapshot(issue)
    ):
        return None
    stamp_issue_readiness(issue)
    target = api_root / "journals" / journal_id / "issues" / f"{issue_id}.json"
    existing = read_json(target)
    if existing is not None:
        identity_fields = ("issue_id", "journal_id", "volume", "issue")
        if any(
            str(existing.get(field, "")).casefold()
            != str(issue.get(field, "")).casefold()
            for field in identity_fields
        ):
            raise RuntimeError(f"archive collision at {target}")
        existing_state = issue_publication_state(existing)
        candidate_is_ready = (
            issue.get("content_status") == "complete"
            and issue.get("source_status")
            in {"official_verified", "publisher_verified"}
            and issue.get("publication_state") == "ready"
        )
        if correct_jie_163_ready_archive:
            correction_state = _matches_jie_163_official_archive_correction(
                existing,
                issue,
            )
            if correction_state == "current":
                return target
            if existing_state != "ready" or correction_state != "stale":
                raise ValueError(
                    "JIE 163 READY archive correction failed its exact source/history gate"
                )
            replacement = copy.deepcopy(issue)
            _preserve_jie_163_archive_observation(existing, replacement)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(
                json.dumps(replacement, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)
            readback = read_json(target)
            if (
                readback is None
                or _doi_sequence(readback)
                != tuple(doi.casefold() for doi in JIE_163_OFFICIAL_RESEARCH_DOIS)
                or _excluded_doi_sequence(readback)
                != tuple(doi.casefold() for doi in JIE_163_OFFICIAL_EXCLUDED_DOIS)
                or issue_source_status(readback) != "official_verified"
            ):
                raise RuntimeError("JIE 163 archive correction read-back failed")
            return target
        if existing_state == "ready":
            return target
        if not replace_non_ready or not candidate_is_ready:
            return target
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(issue, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return target
    if correct_jie_163_ready_archive:
        raise ValueError("JIE 163 correction expected the known stale READY archive")
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
    for pattern in ("%Y-%m-%d", "%Y-%m", "%B %Y", "%b %Y", "%Y"):
        try:
            parsed = datetime.strptime(raw, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        seasonal = re.fullmatch(
            r"(Spring|Summer|Fall|Autumn|Winter)\s+(20\d{2})",
            raw,
            flags=re.IGNORECASE,
        )
        if seasonal:
            month = {
                "spring": 3,
                "summer": 6,
                "fall": 9,
                "autumn": 9,
                "winter": 12,
            }[seasonal.group(1).casefold()]
            parsed = datetime(int(seasonal.group(2)), month, 1)
    if parsed is None:
        chinese = re.fullmatch(r"(\d{4})\s*年\s*(\d{1,2})\s*月", raw)
        if chinese:
            parsed = datetime(int(chinese.group(1)), int(chinese.group(2)), 1)
    if parsed is not None and len(raw.strip()) == 4:
        # Year-only date (e.g. RePEc without month): sort mid-year by volume.
        parsed = parsed.replace(month=6, day=15)
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
        stamp_issue_readiness(issue)
        entries.append(
            {
                "issue_id": issue["issue_id"],
                "volume": issue.get("volume", ""),
                "issue": issue.get("issue", ""),
                "issue_label": archive_issue_label(issue),
                "publication_date": issue.get("publication_date", ""),
                "retrieved_at": issue.get("retrieved_at", ""),
                "article_count": issue.get("research_article_count", 0),
                "content_status": issue["content_status"],
                "source_status": issue["source_status"],
                "publication_state": issue["publication_state"],
                "url": (
                    f"/journals/api/v1/journals/{journal_id}/issues/"
                    f"{issue['issue_id']}.json"
                ),
            }
        )
    entries.sort(key=archive_publication_sort_key, reverse=True)
    index_path = api_root / "journals" / journal_id / "issues" / "index.json"
    index_payload = {
        "schema_version": "1.0",
        "journal_id": journal_id,
        "journal_name": journal_name,
        "updated_at": updated_at,
        "issue_count": len(entries),
        "current_url": (
            f"/journals/api/v1/journals/{journal_id}/issues/current.json"
        ),
        "issues": entries,
    }
    previous = read_json(index_path)
    if previous is not None and "updated_at" in previous:
        # Rebuild time is not evidence of a fresh publisher observation.
        # Keep byte-identical indexes (and their original timestamp) when
        # only the build's updated_at would change.
        without_build_timestamp = lambda payload: {
            key: value for key, value in payload.items() if key != "updated_at"
        }
        if without_build_timestamp(previous) == without_build_timestamp(index_payload):
            return
    write_json(index_path, index_payload)


def search_record(
    issue: dict[str, Any],
    article: dict[str, Any],
    journal: dict[str, Any],
) -> dict[str, Any]:
    relevance = article.get("china_relevance") or classify_china_relevance(article)
    record = {
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
        "content_status": issue_content_status(issue),
        "source_status": issue_source_status(issue),
        "publication_state": issue_publication_state(issue),
    }
    if issue.get("public_mode") == B8_METADATA_MODE:
        record["public_mode"] = B8_METADATA_MODE
        record["public_rights"] = article["public_rights"]
    return record


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
            stamp_issue_readiness(archived)
            if (
                is_publishable_snapshot(archived)
                and issue_publication_state(archived) == "ready"
            ):
                by_issue_id[archived["issue_id"]] = archived
        if current and is_publishable_snapshot(current):
            stamp_issue_readiness(current)
            if issue_publication_state(current) == "ready":
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

    def _year_of(record: dict[str, Any]) -> int | None:
        match = re.search(r"(20\d{2})", str(record.get("publication_date", "")))
        return int(match.group(1)) if match else None

    # China-related latest papers are the default destination of the
    # "中国相关论文" entry. Keeping them in one small file avoids downloading
    # the full latest index before the first result appears.
    china_latest = [record for record in latest_records if record.get("china_related")]
    write_json(
        search_root / "china-latest.json",
        {
            "schema_version": "1.0",
            "scope": "china-latest",
            "updated_at": updated_at,
            "record_count": len(china_latest),
            "records": china_latest,
        },
    )

    # Split the history index by publication year so a filtered search only
    # downloads the year it needs, and an unfiltered history search can load
    # newest-first without waiting for the whole 27 MB all.json.
    by_year: dict[int, list[dict[str, Any]]] = {}
    for record in history_records:
        year = _year_of(record)
        if year is None:
            continue
        by_year.setdefault(year, []).append(record)

    year_entries: list[dict[str, Any]] = []
    for year in sorted(by_year, reverse=True):
        year_records = by_year[year]
        year_issue_ids = {record.get("issue_id") for record in year_records}
        write_json(
            search_root / "years" / f"{year}.json",
            {
                "schema_version": "1.0",
                "scope": "year",
                "year": year,
                "updated_at": updated_at,
                "issue_count": len(year_issue_ids),
                "record_count": len(year_records),
                "records": year_records,
            },
        )
        year_entries.append(
            {
                "year": year,
                "url": f"/journals/api/v1/search/years/{year}.json",
                "issue_count": len(year_issue_ids),
                "record_count": len(year_records),
            }
        )

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
            "schema_version": "1.1",
            "updated_at": updated_at,
            "latest_count": len(latest_records),
            "history_count": len(history_records),
            "issue_count": issue_count,
            "china_latest_count": len(china_latest),
            "latest_url": "/journals/api/v1/search/latest.json",
            "all_url": "/journals/api/v1/search/all.json",
            "china_latest_url": "/journals/api/v1/search/china-latest.json",
            "years": year_entries,
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
    if issue.get("public_mode") == B8_METADATA_MODE:
        content_only |= {"abstract_en_incomplete", "rights_qualified_metadata"}
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
    if issue.get("public_mode") == B8_METADATA_MODE:
        try:
            validate_public_projection(issue)
        except (TypeError, ValueError, KeyError):
            return False
        return True
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

        # Crossref's deposited JATS may prepend a display-only "Abstract "
        # heading on a later fetch. Restore the already-verified exact source
        # only if stripping that heading reproduces the earlier paragraph
        # byte-for-byte for the same DOI and title; all substantive edits must
        # continue to invalidate translations and the semantic numeric gate.
        if (
            doi in existing_by_doi
            and str(article.get("title_en", "")).strip()
            == str(old.get("title_en", "")).strip()
            and str(article.get("sources", {}).get("abstract_en", "")).casefold()
            == "crossref"
            and str(article.get("abstract_en", "")).startswith("Abstract ")
            and str(article["abstract_en"])[len("Abstract "):] == old_abstract_en
        ):
            article["abstract_en"] = old_abstract_en

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
        normalize_no_abstract_comment(article)

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


PII_IN_URL = re.compile(r"/pii/(S\d{15}[0-9X])", re.IGNORECASE)


def refresh_elsevier_abstracts(
    issue: dict[str, Any],
    config: dict[str, Any],
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    """Force Elsevier API abstract lookups without touching roster or order.

    Used by --re-enrich-elsevier. Replaces fallback abstracts (OpenAlex,
    Semantic Scholar, preview snippets) with the publisher metadata abstract
    when the API returns one. A failed or teaser-only lookup keeps the
    existing abstract. Roster, article order, and quality flags are untouched.
    """

    if config.get("collector") != "elsevier":
        return issue
    import requests

    from collectors.metadata_fallback import (
        _defer_elsevier_entitlement,
        _elsevier_lookup,
        _is_elsevier_identifier,
    )

    session = requests.Session()
    articles = issue.get("articles", [])
    replaced = 0
    for article in articles:
        doi = str(article.get("doi", "")).strip().lower()
        url = str(article.get("source_url", ""))
        match = PII_IN_URL.search(url)
        pii = match.group(1).upper() if match else ""
        if not _is_elsevier_identifier(pii, doi):
            continue
        if _defer_elsevier_entitlement(article):
            continue
        lookup = _elsevier_lookup(session, pii, doi=doi, timeout=timeout)
        fetched = str(lookup.get("abstract", "")).strip()
        if not fetched:
            continue
        sources = article.setdefault("sources", {})
        sources["abstract_en"] = str(lookup.get("source", "elsevier-api"))
        if lookup.get("source_url"):
            sources["elsevier"] = lookup["source_url"]
        sources["abstract_lookup"] = {
            "status": str(lookup.get("status", "")),
            "attempts": lookup.get("attempts", []),
        }
        if lookup.get("rate_limit"):
            sources["abstract_lookup"]["rate_limit"] = lookup["rate_limit"]
        if lookup.get("quota_warning"):
            sources["abstract_lookup"]["quota_warning"] = lookup["quota_warning"]
        article["abstract_en"] = fetched
        replaced += 1
    if replaced:
        quality = issue.get("quality", {})
        quality["abstract_en_complete"] = sum(
            bool(str(article.get("abstract_en", "")).strip())
            for article in articles
        )
        if "abstract_en_missing" in quality.get("flags", []):
            quality["flags"] = [
                flag
                for flag in quality["flags"]
                if flag != "abstract_en_missing"
            ]
    return issue


def apply_configured_current_roster(
    issue: dict[str, Any],
    key: str,
    config: dict[str, Any],
    *,
    existing_issue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not config.get("official_roster_evidence_path"):
        return issue
    if key == "RESTAT" and config.get("id") == "restat":
        from scripts.restat_current_roster import apply_restat_current_roster as apply_current_roster
    elif key == "JF" and config.get("id") == "jf":
        from scripts.afa_current_roster import apply_jf_current_roster as apply_current_roster
    elif key == "JEH" and config.get("id") == "jeh":
        from scripts.cambridge_current_roster import apply_jeh_current_roster as apply_current_roster
    elif key == "LANDECON" and config.get("id") == "landecon":
        from scripts.uwpress_current_roster import apply_landecon_current_roster as apply_current_roster
    elif key == "JEEM" and config.get("id") == "jeem":
        from scripts.jeem_current_roster import apply_jeem_current_roster as apply_current_roster
    elif key == "JIE" and config.get("id") == "jie":
        from scripts.jie_current_roster import apply_jie_current_roster as apply_current_roster
    else:
        from scripts.official_current_roster import apply_current_roster

    state = read_json(MONITOR_STATE_PATH) or {}
    journal_states = state.get("journals") if isinstance(state, dict) else None
    journal_state = journal_states.get(key) if isinstance(journal_states, dict) else None
    monitored = journal_state.get("candidate") if isinstance(journal_state, dict) else None
    if not isinstance(monitored, dict):
        monitored = {}
    detected_dois = None
    if str(monitored.get("volume", "")) == str(issue.get("volume", "")):
        # unseen_dois is a delta, not the full roster after a partial release.
        # Reconstruct the known same-issue membership before checking evidence.
        unseen = monitored.get("unseen_dois")
        if isinstance(unseen, list):
            observed = set(map(str.casefold, unseen))
            excluded: set[str] = set()
            baseline = read_json(public_issue_path(config["id"])) or {}
            if baseline.get("issue_id") == issue.get("issue_id"):
                observed.update(str(a.get("doi", "")).casefold() for a in baseline.get("articles", []))
                excluded = {str(a.get("doi", "")).casefold() for a in baseline.get("quality", {}).get("excluded_items", []) if a.get("doi")}
            if monitored.get("doi_count") is not None and len(observed) != monitored["doi_count"]:
                raise ValueError("monitor DOI delta cannot reconstruct the complete candidate")
            detected_dois = sorted(observed | excluded)
    if key == "JIE" and config.get("id") == "jie":
        return apply_current_roster(
            issue,
            config,
            detected_dois=detected_dois,
            existing_issue=existing_issue,
        )
    return apply_current_roster(issue, config, detected_dois=detected_dois)


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
    correct_jie_163_ready_archive: bool = False,
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
    translation_report: dict[str, Any] | None = None
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
                issue = collector_for(
                    config,
                    expected_volume=expected_volume,
                    expected_issue=expected_issue,
                )()
                issue = apply_configured_current_roster(
                    issue, key, config, existing_issue=previous
                )
                if re_enrich_elsevier:
                    issue = refresh_elsevier_abstracts(issue, config)
                if not is_detected_snapshot(issue):
                    raise ValueError(
                        "primary collector failed roster gate: "
                        + ", ".join(
                            structural_flags(issue) or ["untrusted official roster"]
                        )
                    )
            except Exception as error:
                primary_error = f"{type(error).__name__}: {error}"
                fallback = fallback_collector_for(
                    config,
                    expected_volume=expected_volume,
                    expected_issue=expected_issue,
                )
                if fallback is None:
                    raise
                issue = fallback()
                if re_enrich_elsevier:
                    issue = refresh_elsevier_abstracts(issue, config)
                report["primary_error"] = primary_error
                report["transport"] = "metadata_fallback"
                issue = apply_configured_current_roster(
                    issue, key, config, existing_issue=previous
                )
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
            missing_abstract_dois = [
                str(article.get("doi", ""))
                for article in issue.get("articles", [])
                if not str(article.get("abstract_en", "")).strip()
                and str(article.get("doi", "")).strip()
            ]
            raise ValueError(
                "collector result failed the publication gate: "
                + ", ".join(structural_flags(issue) or ["empty official roster"])
                + (
                    "; missing abstracts: " + ", ".join(missing_abstract_dois)
                    if missing_abstract_dois
                    else ""
                )
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
            untranslated_dois = [
                str(article.get("doi", ""))
                for article in issue.get("articles", [])
                if not str(article.get("abstract_cn", "")).strip()
                and str(article.get("doi", "")).strip()
            ]
            raise ValueError(
                f"translation incomplete: {translated}/{total}; "
                "preserving the last complete public issue"
                + (
                    "; untranslated: " + ", ".join(untranslated_dois)
                    if untranslated_dois
                    else ""
                )
            )
        if previous and previous.get("issue_id") != issue.get("issue_id"):
            archive_issue(previous)
        stamp_issue_readiness(issue)
        if issue["publication_state"] != "ready":
            raise ValueError(
                "issue content is complete but source authority is not publication-ready"
            )
        write_json(target, issue)
        write_detected_snapshot(issue)
        readback = read_json(target)
        if readback is None or readback.get("issue_id") != issue["issue_id"]:
            raise RuntimeError("public issue write-back verification failed")
        archive_issue(
            issue,
            correct_jie_163_ready_archive=(
                correct_jie_163_ready_archive
                and key == "JIE"
                and config.get("id") == "jie"
            ),
        )
        if correct_jie_163_ready_archive and key == "JIE":
            report["archive_correction"] = (
                "jie-163 source-qualified READY archive reconciled"
            )
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
        if isinstance(translation_report, dict) and translation_report.get(
            "failed"
        ):
            report["translation_failures"] = translation_report["failed"]
        return previous, report


def issue_translation_semantics_valid(issue: dict[str, Any]) -> bool:
    """Return whether public translation fields satisfy their mode-specific gate."""
    if issue.get("public_mode") == B8_METADATA_MODE:
        try:
            validate_public_projection(issue)
        except (TypeError, ValueError, KeyError):
            return False
        return True
    for article in issue.get("articles", []):
        if not translation_is_complete(article):
            return False
        if not article.get("abstract_en"):
            continue
        try:
            validate_translation(
                article,
                {
                    "title_cn": article.get("title_cn", ""),
                    "abstract_cn": article.get("abstract_cn", ""),
                },
            )
        except TranslationError:
            return False
    return True


def _ordered_issue_dois(issue: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(article.get("doi", "")).strip().casefold()
        for article in issue.get("articles", [])
        if str(article.get("doi", "")).strip()
    )


_SOURCE_ONLY_FLAGS = {
    "crossref_provisional_roster",
    "publisher_html_blocked_crossref_fallback",
    "publisher_rss_lag_crossref_fallback",
    "official_order_unverified",
}


def _lift_verified_same_roster_authority(
    current: dict[str, Any],
    archived: dict[str, Any],
) -> dict[str, Any] | None:
    """Keep current content while restoring stronger verified membership/order evidence.

    Source authority describes issue membership/order; it must not force older
    metadata or translations back onto readers.  Promotion is allowed only for
    the exact same issue identity and exact ordered DOI roster.
    """

    current_dois = _ordered_issue_dois(current)
    archived_dois = _ordered_issue_dois(archived)
    if not current_dois or current_dois != archived_dois:
        return None
    if issue_publication_state(archived) != "ready":
        return None

    merged = copy.deepcopy(current)
    merged_quality = merged.setdefault("quality", {})
    archived_quality = archived.get("quality", {})
    for key in (
        "roster_authority",
        "roster_transport",
        "roster_match_scope",
        "official_roster_evidence",
        "browser_capture",
        "browser_order_verification",
        "rss_url",
    ):
        if key in archived_quality:
            merged_quality[key] = copy.deepcopy(archived_quality[key])
    merged_quality["roster_match"] = True
    merged_quality["order_preserved"] = True
    merged_quality["flags"] = [
        flag
        for flag in merged_quality.get("flags", [])
        if str(flag) not in _SOURCE_ONLY_FLAGS
    ]
    if archived.get("source_url"):
        merged["source_url"] = archived["source_url"]
    return stamp_issue_readiness(merged)


def prefer_ready_archive(
    current: dict[str, Any],
    archived: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use verified same-issue authority without regressing current content."""

    if not archived or current.get("issue_id") != archived.get("issue_id"):
        return current
    if issue_publication_state(current) == "ready":
        return current
    lifted = _lift_verified_same_roster_authority(current, archived)
    if lifted is not None:
        return lifted
    return current

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
        if issue is None:
            # A brand-new journal may not have a live current snapshot yet
            # (publisher endpoint lag or an in-progress volume). Backfilled
            # archives are the best available "latest" data and keep the
            # journal available; the site already prefers the newest archive.
            issue = _newest_publishable_archived(config["id"])
        if issue:
            # Prefer the newest publishable archived issue when it is newer
            # than the detected current snapshot. The backfill archives new
            # issues as soon as Crossref/official data arrives, while the
            # publisher "current" detection can lag behind; the site's
            # "latest" label must not point at an older issue than the list.
            newest_archived = _newest_publishable_archived(config["id"])
            issue = prefer_ready_archive(issue, newest_archived)
            current_sort_key = archive_publication_sort_key(issue)
            if (
                newest_archived
                and current_sort_key[0] > 0
                and archive_publication_sort_key(newest_archived)
                > current_sort_key
            ):
                # Only promote the archive when the detected current issue has
                # a parseable publication date; an unparseable label such as
                # "Summer 2026" must not sort as year zero and lose to an
                # older archived issue.
                issue = newest_archived
            try:
                issue = normalize_issue_content(issue)
                validate_issue(issue)
            except ValueError:
                continue
            stamp_issue_readiness(issue)
            write_json(public_issue_path(config["id"]), issue)
            available[key] = issue
    return available


def _newest_publishable_archived(
    journal_id: str,
    *,
    api_root: Path | None = None,
) -> dict[str, Any] | None:
    """Return the newest publishable archive for a journal, if any."""
    if api_root is None:
        api_root = PUBLIC_API
    candidates = [
        issue
        for issue in archived_issues(journal_id, api_root=api_root)
        if is_publishable_snapshot(issue)
        and issue_publication_state(issue) == "ready"
    ]
    if not candidates:
        return None
    return max(candidates, key=archive_publication_sort_key)


def update_indexes(
    journal_configs: dict[str, dict[str, Any]],
    issues: dict[str, dict[str, Any]],
    *,
    archive_current: bool = True,
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
    rights_withheld_articles = 0
    monitor_state = read_json(MONITOR_STATE_PATH) or {}
    monitor_entries = monitor_state.get("journals", {})

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
            stamp_issue_readiness(detected)
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
                    "latest_detected_content_status": detected["content_status"],
                    "latest_detected_source_status": detected["source_status"],
                    "latest_detected_publication_state": detected["publication_state"],
                    "update_state": (
                        detected["publication_state"]
                        if detected["publication_state"] != "blocked"
                        else "enriching"
                    ),
                }
            )
        if issue and is_publishable_snapshot(issue):
            stamp_issue_readiness(issue)
            usable_count += 1
            translated = issue["quality"]["translation_complete"]
            total = issue["research_article_count"]
            translated_articles += translated
            total_articles += total
            if issue.get("public_mode") == B8_METADATA_MODE:
                withheld = issue["public_content"]["rights_withheld_articles"]
                rights_withheld_articles += withheld
                entry["public_mode"] = B8_METADATA_MODE
                entry["rights_withheld_articles"] = withheld
            entry.update(
                {
                    "status": issue["status"],
                    "data_status": (
                        "healthy" if not structural_flags(issue) else "needs_attention"
                    ),
                    "content_status": issue["content_status"],
                    "source_status": issue["source_status"],
                    "publication_state": issue["publication_state"],
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
                        "latest_detected_content_status": issue["content_status"],
                        "latest_detected_source_status": issue["source_status"],
                        "latest_detected_publication_state": issue["publication_state"],
                        "update_state": issue["publication_state"],
                    }
                )
            checks[f"{config['id']}_roster_match"] = issue["quality"]["roster_match"]
            checks[f"{config['id']}_order_preserved"] = issue["quality"]["order_preserved"]
            checks[f"{config['id']}_primary_transport"] = not bool(
                structural_flags(issue)
            )
        else:
            checks[f"{config['id']}_available"] = False
        display_source = select_display_issue(
            detected if detected and is_detected_snapshot(detected) else None,
            issue if issue and is_publishable_snapshot(issue) else None,
        )
        if display_source == "detected":
            entry.update(
                {
                    "latest_display_issue_id": entry.get("latest_detected_issue_id", ""),
                    "latest_display_issue_url": entry.get("latest_detected_issue_url", ""),
                    "latest_display_issue_label": entry.get("latest_detected_issue_label", ""),
                    "latest_display_publication_date": entry.get("latest_detected_publication_date", ""),
                    "latest_display_publication_state": entry.get("latest_detected_publication_state", ""),
                    "latest_display_source": "detected",
                }
            )
        elif display_source == "ready":
            entry.update(
                {
                    "latest_display_issue_id": entry.get("latest_ready_issue_id", entry.get("latest_issue_id", "")),
                    "latest_display_issue_url": entry.get("latest_ready_issue_url", entry.get("latest_issue_url", "")),
                    "latest_display_issue_label": entry.get("latest_issue_label", ""),
                    "latest_display_publication_date": entry.get("publication_date", ""),
                    "latest_display_publication_state": entry.get("publication_state", "ready"),
                    "latest_display_source": "ready",
                }
            )
        monitor_entry = (
            monitor_entries.get(key)
            or monitor_entries.get(config["id"])
            or {}
        )
        entry.update(
            announcement_entry_fields(
                config["id"],
                monitor_entry.get("announcement"),
                ready=(
                    issue
                    if issue and is_publishable_snapshot(issue)
                    else None
                ),
                detected=(
                    detected
                    if detected and is_detected_snapshot(detected)
                    else None
                ),
            )
        )
        journal_entries[key] = entry
        if issue and archive_current:
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
        collection_withheld = sum(int(entry.get("rights_withheld_articles", 0)) for entry in entries)
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
            "content_status": (
                "rights_qualified" if collection_withheld else
                "complete" if collection_content_complete else "translation_incomplete"
            ),
            "summary": {
                "configured_journals": len(entries),
                "available_journals": collection_usable,
                "articles": collection_articles,
                "translated_articles": collection_translated,
                **({"rights_withheld_articles": collection_withheld} if collection_withheld else {}),
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
        "content_status": (
            "rights_qualified" if rights_withheld_articles else
            "complete" if content_complete else "translation_incomplete"
        ),
        "summary": {
            "enabled_journals": enabled_count,
            "available_journals": usable_count,
            "articles": total_articles,
            "translated_articles": translated_articles,
            **({"rights_withheld_articles": rights_withheld_articles} if rights_withheld_articles else {}),
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
        "journal_count": enabled_count,
        "available_journal_count": usable_count,
        "field_journal_count": max(0, enabled_count - 5),
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
    parser.add_argument(
        "--correct-jie-163-ready-archive",
        action="store_true",
        help="Apply the single bounded G16-approved correction to the known stale "
        "JIE 163 READY archive; requires --journal JIE.",
    )
    args = parser.parse_args()
    if args.correct_jie_163_ready_archive and args.journal != "JIE":
        parser.error("--correct-jie-163-ready-archive requires --journal JIE")

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
            correct_jie_163_ready_archive=args.correct_jie_163_ready_archive,
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
        "correct_jie_163_ready_archive": args.correct_jie_163_ready_archive,
        "results": reports,
        "translation_provider_state": translation_provider_state,
        "available_journals": sorted(available),
    }
    write_json(UPDATE_REPORT, final_report)
    return _update_exit_code(reports, available)


def _update_exit_code(
    reports: list[dict[str, Any]],
    available: dict[str, dict[str, Any]],
) -> int:
    failed_journals = {
        str(report.get("journal_id") or report.get("journal", "")).casefold()
        for report in reports
        if report["result"] == "failed"
    }
    # A brand-new journal whose live current collection fails (e.g. an
    # in-progress volume beyond the publication horizon) is still usable when
    # the archive fallback produced a current snapshot. Do not block the whole
    # data publish for that transitional state; a journal with no data at all
    # still fails the run.
    return 1 if failed_journals - {key.casefold() for key in available} else 0


if __name__ == "__main__":
    raise SystemExit(main())
