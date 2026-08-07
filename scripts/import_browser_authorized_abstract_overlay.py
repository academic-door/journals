from __future__ import annotations

import argparse
import html
import json
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.article_types import normalize_issue_taxonomy
from scripts.translate_issue import (
    PROMPT_VERSION,
    _canonicalize_arabic_numbers,
    _normalize_written_number_translations,
    _repair_google_artifacts,
    _source_hash,
    _write_cache,
    translate_missing,
    validate_translation,
)
from scripts.update_journals import (
    TRANSLATION_CACHE,
    apply_translation_cache,
    is_archivable_snapshot,
    normalize_issue_content,
    validate_issue,
    write_json,
)

ALLOWED_HOSTS = {
    "sciencedirect.com",
    "www.sciencedirect.com",
    "link.springer.com",
    "journals.uchicago.edu",
    "onlinelibrary.wiley.com",
    "academic.oup.com",
    "direct.mit.edu",
    "cambridge.org",
    "le.uwpress.org",
}
FORBIDDEN_KEY_PARTS = {
    "authorization",
    "cookie",
    "credential",
    "localstorage",
    "password",
    "session",
    "token",
}
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def clean_official_abstract(value: object) -> str:
    text = str(value or "").strip()
    # ScienceDirect previews expose MathML. Keep its readable symbols unless
    # they merely duplicate a formula already rendered immediately before it.
    math_pattern = re.compile(r"<math\b[^>]*>(.*?)</math>", re.IGNORECASE | re.DOTALL)

    def replace_math(match: re.Match[str]) -> str:
        formula = re.sub(
            r"\s+",
            "",
            re.sub(r"<[^>]+>", "", html.unescape(match.group(1))),
        )
        prefix = re.sub(r"\s+", "", html.unescape(match.string[: match.start()]))
        return "" if formula and prefix.endswith(formula) else f" {formula} "

    text = math_pattern.sub(replace_math, text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _walk_keys(value: object, prefix: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            is_false_privacy_attestation = (
                prefix == "privacy."
                and key in {"cookies_stored", "credentials_stored", "session_data_stored"}
                and nested is False
            )
            if (
                any(part in normalized for part in FORBIDDEN_KEY_PARTS)
                and not is_false_privacy_attestation
            ):
                errors.append(f"forbidden private field: {prefix}{key}")
            errors.extend(_walk_keys(nested, f"{prefix}{key}."))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            errors.extend(_walk_keys(nested, f"{prefix}{index}."))
    return errors


def _official_url(value: object) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def validate_overlay(overlay: dict[str, Any]) -> None:
    errors = _walk_keys(overlay)
    if overlay.get("capture_mode") != "browser-authorized-abstract-overlay":
        errors.append("capture_mode must be browser-authorized-abstract-overlay")
    if not overlay.get("journal_id"):
        errors.append("journal_id missing")
    if not overlay.get("captured_at"):
        errors.append("captured_at missing")
    privacy = overlay.get("privacy", {})
    if not isinstance(privacy, dict) or any(privacy.values()):
        errors.append("privacy fields must all be false")
    items = overlay.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items must be a non-empty array")
    else:
        seen: set[str] = set()
        for index, item in enumerate(items, start=1):
            doi = str(item.get("doi", "")).strip().lower()
            if not DOI_RE.fullmatch(doi):
                errors.append(f"item {index}: invalid DOI")
            elif doi in seen:
                errors.append(f"item {index}: duplicate DOI {doi}")
            seen.add(doi)
            if not str(item.get("title_en") or item.get("title") or "").strip():
                errors.append(f"item {index}: title missing")
            if not str(item.get("abstract_en", "")).strip():
                errors.append(f"item {index}: abstract_en missing")
            if not _official_url(item.get("source_url")):
                errors.append(f"item {index}: source_url is not official ScienceDirect")
            issue_url = item.get("issue_source_url")
            if issue_url and not _official_url(issue_url):
                errors.append(f"item {index}: issue_source_url is not official ScienceDirect")
    if errors:
        raise ValueError("overlay source-integrity gate failed:\n" + "\n".join(errors))


def apply_overlay(issue: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    validate_overlay(overlay)
    candidate = deepcopy(issue)
    journal_id = str(candidate.get("journal_id", ""))
    if journal_id != str(overlay.get("journal_id", "")):
        raise ValueError("overlay journal_id does not match the base issue")

    by_doi = {
        str(article.get("doi", "")).strip().lower(): article
        for article in candidate.get("articles", [])
        if article.get("doi")
    }
    applied: list[str] = []
    for item in overlay["items"]:
        doi = str(item["doi"]).strip().lower()
        article = by_doi.get(doi)
        if article is None:
            raise ValueError(f"overlay DOI is absent from base issue: {doi}")
        overlay_title = str(item.get("title_en") or item.get("title") or "").strip()
        if overlay_title.casefold() != str(article.get("title_en", "")).strip().casefold():
            raise ValueError(f"overlay title does not match base issue for {doi}")
        article["abstract_en"] = clean_official_abstract(item["abstract_en"])
        article["source_url"] = str(item["source_url"])
        article.setdefault("sources", {})["abstract_en"] = (
            "official-sciencedirect-issue-preview"
        )
        article["sources"]["metadata"] = str(item["source_url"])
        article["abstract_lookup"] = {
            "status": "browser_authorized_official_complete",
            "source_url": str(item["source_url"]),
            "captured_at": str(item.get("captured_at") or overlay["captured_at"]),
        }
        article["quality_flags"] = [
            flag for flag in article.get("quality_flags", [])
            if flag != "abstract_en_missing"
        ]
        applied.append(doi)

    candidate.setdefault("quality", {})["browser_authorized_abstracts"] = {
        "captured_at": str(overlay["captured_at"]),
        "institutional_access_confirmed": all(
            bool(item.get("institutional_access_confirmed"))
            for item in overlay["items"]
        ),
        "privacy_fields_stored": False,
        "applied_count": len(applied),
    }
    candidate["quality"]["roster_transport"] = (
        str(candidate["quality"].get("roster_transport", ""))
        + "+browser-authorized-abstracts"
    ).strip("+")
    candidate = normalize_issue_content(candidate)
    validate_issue(candidate)
    return candidate


def exclude_official_item_without_abstract(
    issue: dict[str, Any],
    *,
    doi: str,
    title_en: str,
    source_url: str,
    raw_type: str,
    reason: str,
) -> dict[str, Any]:
    """Exclude only an official non-article item whose publisher supplies no Abstract."""

    if not _official_url(source_url):
        raise ValueError("excluded item source_url is not official ScienceDirect")
    normalized_doi = doi.strip().lower()
    matches = [
        article for article in issue.get("articles", [])
        if str(article.get("doi", "")).strip().lower() == normalized_doi
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one base item for exclusion: {normalized_doi}")
    article = matches[0]
    if str(article.get("title_en", "")).strip().casefold() != title_en.strip().casefold():
        raise ValueError("excluded item title does not match base issue")
    if str(article.get("abstract_en", "")).strip():
        raise ValueError("refusing to exclude an item that already has an abstract")

    issue["articles"] = [item for item in issue["articles"] if item is not article]
    for sequence, item in enumerate(issue["articles"], start=1):
        item["sequence"] = sequence
    quality = issue.setdefault("quality", {})
    excluded = quality.setdefault("excluded_items", [])
    excluded.append({
        "title_en": title_en.strip(),
        "article_type": "short_communication",
        "raw_type": raw_type.strip(),
        "doi": normalized_doi,
        "source_url": source_url,
        "source_sequence": article.get("source_sequence", article.get("sequence")),
        "reason": reason,
    })
    count = len(issue["articles"])
    issue["expected_article_count"] = count
    issue["research_article_count"] = count
    quality["roster_match"] = True
    quality["order_preserved"] = True
    quality["official_item_count"] = count + len(excluded)
    issue = normalize_issue_taxonomy(issue)
    issue = normalize_issue_content(issue)
    quality = issue["quality"]
    quality["doi_complete"] = sum(bool(item.get("doi")) for item in issue["articles"])
    quality["authors_complete"] = sum(
        bool(item.get("authors")) for item in issue["articles"]
    )
    validate_issue(issue)
    return issue


def import_browser_translation_results(
    issue: dict[str, Any],
    payload: dict[str, Any],
    cache_path: Path,
) -> int:
    errors = _walk_keys(payload)
    if payload.get("capture_mode") != "browser-authorized-google-translate-ui":
        errors.append("capture_mode must be browser-authorized-google-translate-ui")
    if payload.get("journal_id") != issue.get("journal_id"):
        errors.append("translation result journal_id does not match issue")
    privacy = payload.get("privacy", {})
    if not isinstance(privacy, dict) or any(privacy.values()):
        errors.append("translation result privacy fields must all be false")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        errors.append("translation result items must be a non-empty array")
    if errors:
        raise ValueError("browser translation gate failed:\n" + "\n".join(errors))

    by_doi = {
        str(article.get("doi", "")).strip().lower(): article
        for article in issue.get("articles", [])
    }
    cache = read_json(cache_path) if cache_path.exists() else {}
    imported = 0
    for item in items:
        doi = str(item.get("doi", "")).strip().lower()
        article = by_doi.get(doi)
        if article is None:
            raise ValueError(f"translation result DOI is absent from issue: {doi}")
        translated = {
            "title_cn": _repair_google_artifacts(
                article["title_en"],
                _canonicalize_arabic_numbers(
                    article["title_en"],
                    _normalize_written_number_translations(
                        article["title_en"], str(item.get("title_cn", ""))
                    ),
                ),
            ),
            "abstract_cn": _repair_google_artifacts(
                article["abstract_en"],
                _canonicalize_arabic_numbers(
                    article["abstract_en"],
                    _normalize_written_number_translations(
                        article["abstract_en"], str(item.get("abstract_cn", ""))
                    ),
                ),
            ),
        }
        validate_translation(article, translated)
        cache[doi] = {
            **cache.get(doi, {}),
            **translated,
            "source_hash": _source_hash(article),
            "translation": {
                "provider": "google-translate-browser-ui",
                "model": "google-translate-web",
                "prompt_version": PROMPT_VERSION,
                "translated_at": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat(),
            },
        }
        imported += 1
    _write_cache(cache_path, cache)
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge privacy-safe official publisher abstract overlays into issues."
    )
    parser.add_argument("base", type=Path)
    parser.add_argument("overlay", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--translation-cache", type=Path)
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--google-timeout", type=int, default=90)
    parser.add_argument("--browser-translations", type=Path)
    parser.add_argument(
        "--exclude-no-abstract",
        type=Path,
        help="Explicit official item that the publisher labels as a non-article and supplies no Abstract for.",
    )
    args = parser.parse_args()

    issue = apply_overlay(read_json(args.base), read_json(args.overlay))
    if args.exclude_no_abstract:
        exclusion = read_json(args.exclude_no_abstract)
        issue = exclude_official_item_without_abstract(issue, **exclusion)
    cache_path = args.translation_cache or TRANSLATION_CACHE / f"{issue['journal_id']}.json"
    if args.browser_translations:
        import_browser_translation_results(
            issue,
            read_json(args.browser_translations),
            cache_path,
        )
    if args.translate:
        translate_missing(issue, cache_path, google_timeout=args.google_timeout)
    issue = normalize_issue_content(apply_translation_cache(issue, cache_path=cache_path))
    if is_archivable_snapshot(issue):
        issue["status"] = "ready"
        issue["publication_state"] = "ready"
    validate_issue(issue)
    write_json(args.output, issue)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
