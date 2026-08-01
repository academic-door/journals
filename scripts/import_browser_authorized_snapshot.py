from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.article_types import (
    canonical_article_type,
    exclusion_reason,
    is_publishable_type,
    normalize_issue_taxonomy,
    requires_abstract,
)
from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    JOURNALS_PATH,
    TRANSLATION_CACHE,
    apply_translation_cache,
    archive_issue,
    is_archivable_snapshot,
    load_available_issues,
    normalize_issue_content,
    public_issue_path,
    update_indexes,
    validate_issue,
    write_detected_snapshot,
    write_json as write_public_json,
)


DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "browser-authorized"
ALLOWED_HOSTS = {"www.sciencedirect.com", "sciencedirect.com"}
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
PII_RE = re.compile(r"^[A-Z0-9]{10,}$", re.IGNORECASE)
TRUNCATED_AUTHOR_RE = re.compile(r"(?:\.\.\.|…|\bet\s+al\.?)", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("snapshot must be a JSON object")
    return payload


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _walk_keys(value: object, prefix: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                findings.append(f"forbidden private field: {prefix}{key}")
            findings.extend(_walk_keys(nested, f"{prefix}{key}."))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            findings.extend(_walk_keys(nested, f"{prefix}{index}."))
    return findings


def _official_sciencedirect_url(value: object) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def validate_snapshot(snapshot: dict[str, Any]) -> list[str]:
    errors = _walk_keys(snapshot)
    required = (
        "journal_id",
        "journal_name",
        "volume",
        "issue",
        "publication_date",
        "source_url",
        "captured_at",
        "items",
    )
    for field in required:
        if not snapshot.get(field):
            errors.append(f"missing top-level field: {field}")
    if snapshot.get("capture_mode") != "browser-authorized":
        errors.append("capture_mode must be browser-authorized")
    if not _official_sciencedirect_url(snapshot.get("source_url")):
        errors.append("source_url must be an official ScienceDirect HTTPS URL")
    items = snapshot.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items must be a non-empty array")
        return list(dict.fromkeys(errors))

    seen_pii: set[str] = set()
    seen_doi: set[str] = set()
    for expected_order, item in enumerate(items, start=1):
        label = f"item {expected_order}"
        if not isinstance(item, dict):
            errors.append(f"{label}: expected object")
            continue
        if item.get("official_order") != expected_order:
            errors.append(f"{label}: official_order must be contiguous")
        title = str(item.get("title_en", "")).strip()
        raw_type = str(item.get("raw_type", "")).strip()
        article_type = canonical_article_type(title, raw_type, raw_type=raw_type)
        pii = str(item.get("pii", "")).strip().upper()
        doi = str(item.get("doi", "")).strip().lower()
        authors = item.get("authors")
        abstract = str(item.get("abstract_en", "")).strip()
        if not title:
            errors.append(f"{label}: title_en missing")
        if not PII_RE.fullmatch(pii):
            errors.append(f"{label}: invalid or missing PII")
        elif pii in seen_pii:
            errors.append(f"{label}: duplicate PII {pii}")
        seen_pii.add(pii)
        if not DOI_RE.fullmatch(doi):
            errors.append(f"{label}: invalid or missing DOI")
        elif doi in seen_doi:
            errors.append(f"{label}: duplicate DOI {doi}")
        seen_doi.add(doi)
        if not _official_sciencedirect_url(item.get("source_url")):
            errors.append(f"{label}: source_url is not official ScienceDirect")
        if is_publishable_type(article_type):
            if not isinstance(authors, list) or not authors:
                errors.append(f"{label}: authors missing")
            elif any(
                not str(author).strip()
                or TRUNCATED_AUTHOR_RE.search(str(author))
                for author in authors
            ):
                errors.append(f"{label}: authors are empty or truncated")
            if requires_abstract(article_type) and not abstract:
                errors.append(f"{label}: official English abstract missing")
    return list(dict.fromkeys(errors))


def build_candidate(snapshot: dict[str, Any]) -> dict[str, Any]:
    errors = validate_snapshot(snapshot)
    if errors:
        raise ValueError("snapshot source-integrity gate failed:\n" + "\n".join(errors))

    source_url = str(snapshot["source_url"])
    articles: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in snapshot["items"]:
        title = str(item["title_en"]).strip()
        raw_type = str(item.get("raw_type", "")).strip()
        article_type = canonical_article_type(title, raw_type, raw_type=raw_type)
        doi = str(item["doi"]).strip().lower()
        record = {
            "title_en": title,
            "article_type": article_type,
            "doi": doi,
            "source_url": str(item["source_url"]),
            "source_sequence": int(item["official_order"]),
        }
        if not is_publishable_type(article_type):
            excluded.append({**record, "reason": exclusion_reason(article_type)})
            continue
        articles.append(
            {
                "paper_id": f"doi:{doi}",
                "sequence": len(articles) + 1,
                "source_sequence": int(item["official_order"]),
                "article_type": article_type,
                "title_en": title,
                "title_cn": "",
                "authors": [str(author).strip() for author in item["authors"]],
                "abstract_en": str(item.get("abstract_en", "")).strip(),
                "abstract_cn": "",
                "doi": doi,
                "source_url": str(item["source_url"]),
                "publication_date": str(snapshot["publication_date"]),
                "sources": {
                    "issue": source_url,
                    "roster": "official-sciencedirect-browser",
                    "metadata": str(item["source_url"]),
                    "abstract_en": "official-sciencedirect-issue-preview",
                },
                "translation": {"status": "missing"},
                "quality_flags": ["title_cn_missing", "abstract_cn_missing"],
            }
        )

    issue_id = f"{snapshot['journal_id']}-{snapshot['volume']}-{str(snapshot['issue']).lower()}"
    candidate = {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "journal_id": str(snapshot["journal_id"]),
        "journal_name": str(snapshot["journal_name"]),
        "volume": str(snapshot["volume"]),
        "issue": str(snapshot["issue"]),
        "issue_label": f"Vol. {snapshot['volume']}",
        "publication_date": str(snapshot["publication_date"]),
        "source_url": source_url,
        "retrieved_at": str(snapshot["captured_at"]),
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
            "roster_transport": "browser-authorized-local",
            "official_item_count": len(snapshot["items"]),
            "excluded_items": excluded,
            "doi_complete": len(articles),
            "authors_complete": len(articles),
            "abstract_en_complete": len(articles),
            "translation_complete": 0,
            "duplicate_count": 0,
            "flags": [
                "translation_incomplete",
            ],
            "browser_capture": {
                "captured_at": str(snapshot["captured_at"]),
                "institutional_access_confirmed": bool(
                    snapshot.get("institutional_access_confirmed")
                ),
                "privacy_fields_stored": False,
            },
        },
    }
    normalize_issue_taxonomy(candidate)
    validate_issue(candidate)
    return candidate


def build_gap_report(
    snapshot: dict[str, Any],
    candidate: dict[str, Any],
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    current = current or {}
    current_dois = {
        str(item.get("doi", "")).strip().lower()
        for item in current.get("articles", [])
        if item.get("doi")
    }
    candidate_dois = {item["doi"] for item in candidate["articles"]}
    translated = int(candidate["quality"].get("translation_complete", 0))
    required = int(candidate["research_article_count"])
    translation_ready = translated == required and required > 0
    return {
        "schema_version": "1.0",
        "generated_at": now_iso(),
        "journal_id": candidate["journal_id"],
        "official_issue": {
            "issue_id": candidate["issue_id"],
            "volume": candidate["volume"],
            "publication_date": candidate["publication_date"],
            "official_items": candidate["content_counts"]["official_items"],
            "publishable_items": candidate["content_counts"]["publishable_items"],
            "excluded_items": candidate["content_counts"]["official_items"]
            - candidate["content_counts"]["publishable_items"],
        },
        "current_public_issue": {
            "issue_id": current.get("issue_id", ""),
            "volume": current.get("volume", ""),
            "publication_date": current.get("publication_date", ""),
            "publishable_items": len(current.get("articles", [])),
        },
        "freshness_gap": {
            "new_issue_detected": current.get("issue_id") != candidate["issue_id"],
            "new_publishable_dois": len(candidate_dois - current_dois),
            "overlapping_dois": len(candidate_dois & current_dois),
        },
        "source_integrity_gate": {
            "status": "passed",
            "errors": [],
            "official_order_complete": True,
            "doi_complete": candidate["quality"]["doi_complete"],
            "authors_complete": candidate["quality"]["authors_complete"],
            "abstract_en_complete": candidate["quality"]["abstract_en_complete"],
        },
        "publication_gate": {
            "status": "passed" if translation_ready else "blocked",
            "reason": (
                "All source and translation gates passed"
                if translation_ready
                else "Chinese title and abstract translations are incomplete"
            ),
            "translation_complete": translated,
            "required": required,
        },
        "privacy": {
            "cookies_stored": False,
            "credentials_stored": False,
            "session_data_stored": False,
        },
        "source_snapshot": Path(str(snapshot.get("snapshot_path", ""))).name,
    }


def translate_candidate(
    candidate: dict[str, Any],
    cache_path: Path,
    *,
    session: requests.Session | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = translate_missing(candidate, cache_path, session=session)
    candidate = apply_translation_cache(candidate, cache_path=cache_path)
    candidate = normalize_issue_content(candidate)
    complete = int(candidate["quality"].get("translation_complete", 0))
    total = int(candidate["research_article_count"])
    if complete == total and total > 0:
        candidate["status"] = "ready"
        candidate["publication_state"] = "ready"
    validate_issue(candidate)
    return candidate, report


def promote_candidate(candidate: dict[str, Any]) -> Path:
    if not is_archivable_snapshot(candidate):
        complete = int(candidate.get("quality", {}).get("translation_complete", 0))
        total = int(candidate.get("research_article_count", 0))
        raise ValueError(
            "candidate failed the publication gate: "
            f"translations {complete}/{total}"
        )
    target = public_issue_path(str(candidate["journal_id"]))
    previous = read_json(target) if target.exists() else None
    if previous and previous.get("issue_id") != candidate.get("issue_id"):
        archive_issue(previous)
    write_public_json(target, candidate)
    write_detected_snapshot(candidate)
    archive_issue(candidate)

    configs = yaml.safe_load(JOURNALS_PATH.read_text(encoding="utf-8"))["journals"]
    refreshed_key = next(
        key
        for key, config in configs.items()
        if config.get("id") == candidate["journal_id"]
    )
    issues = load_available_issues(configs, {refreshed_key: candidate})
    update_indexes(configs, issues)
    readback = read_json(target)
    if not readback or readback.get("issue_id") != candidate.get("issue_id"):
        raise RuntimeError("promoted issue write-back verification failed")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a local browser-authorized official issue snapshot and build "
            "a non-publishing Academic Door candidate plus a freshness-gap report."
        )
    )
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--translation-cache", type=Path)
    parser.add_argument(
        "--ignore-proxy-env",
        action="store_true",
        help="Use a direct requests session when inherited proxy variables are broken.",
    )
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        raise RuntimeError("browser-authorized snapshots are local-only")
    snapshot = read_json(args.snapshot)
    snapshot["snapshot_path"] = str(args.snapshot)
    candidate = build_candidate(snapshot)
    translation_report: dict[str, Any] | None = None
    cache_path = args.translation_cache or (
        TRANSLATION_CACHE / f"{candidate['journal_id']}.json"
    )
    if args.translate:
        translation_session = None
        if args.ignore_proxy_env:
            translation_session = requests.Session()
            translation_session.trust_env = False
        candidate, translation_report = translate_candidate(
            candidate,
            cache_path,
            session=translation_session,
        )
    else:
        candidate = normalize_issue_content(
            apply_translation_cache(candidate, cache_path=cache_path)
        )
        complete = int(candidate["quality"].get("translation_complete", 0))
        if complete == int(candidate["research_article_count"]):
            candidate["status"] = "ready"
            candidate["publication_state"] = "ready"
        validate_issue(candidate)
    current = read_json(args.current) if args.current and args.current.exists() else None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = args.output_dir / f"{candidate['issue_id']}.candidate.json"
    report_path = args.output_dir / f"{candidate['issue_id']}.gap-report.json"
    write_json(candidate_path, candidate)
    gap_report = build_gap_report(snapshot, candidate, current)
    if translation_report is not None:
        gap_report["translation_run"] = translation_report
    write_json(report_path, gap_report)
    if args.promote:
        try:
            promoted_path = promote_candidate(candidate)
        except Exception as error:
            gap_report["promotion"] = {
                "status": "blocked",
                "error": f"{type(error).__name__}: {error}",
            }
            write_json(report_path, gap_report)
            raise
        else:
            gap_report["promotion"] = {
                "status": "completed",
                "target": str(promoted_path.relative_to(ROOT)).replace("\\", "/"),
            }
    write_json(report_path, gap_report)
    print(candidate_path)
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
