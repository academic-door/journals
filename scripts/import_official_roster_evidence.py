"""Promote provisional history archives after official-roster matching.

Existing archive articles must be an ordered subset of the official roster.
Official evidence may also carry the authors and abstract needed to restore
missing research articles. Existing audited metadata and translations are
preserved; newly restored articles remain translation-incomplete until the
normal translation gate succeeds.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_journals import (
    PUBLIC_API,
    TRANSLATION_CACHE,
    apply_translation_cache,
    normalize_issue_content,
    stamp_issue_readiness,
    validate_issue,
)
from scripts.translate_issue import translate_missing


ALLOWED_HOSTS = {
    "academic.oup.com",
    "direct.mit.edu",
    "journals.uchicago.edu",
    "le.uwpress.org",
    "onlinelibrary.wiley.com",
    "www.aeaweb.org",
    "www.cambridge.org",
    "www.sciencedirect.com",
}
ALLOWED_METHODS = {"browser-authorized", "official-page-read"}
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


def _normalized_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    # Oxford issue pages expose some display markup literally while deposited
    # metadata keeps the same title as plain text.  Normalize only these narrow
    # presentation artifacts after the DOI sequence has already matched.
    text = re.sub(r"_\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"(?<=[A-Za-z])&(?=[A-Za-z])", "", text)
    # ScienceDirect renders inline MathML as separated visible glyphs in an
    # authorized browser snapshot (for example ``P M 2.5``), while its article
    # metadata uses the equivalent compact token ``PM2.5``.
    text = re.sub(r"\bP\s*M\s*2\s*\.\s*5\b", "PM2.5", text, flags=re.IGNORECASE)
    # Publisher cards and deposited metadata routinely differ only in smart
    # quotes, daggers, hyphenation, spacing, capitalization, or an optional
    # article ("the"). DOI identity and order remain the hard keys; collapse
    # these display-only variants without accepting a materially different
    # sequence.
    text = re.sub(r"(?<=\w)['’](?=\w)", "", text)
    text = re.sub(r"[\"“”‘’†‡]", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    tokens = [token for token in text.casefold().split() if token != "the"]
    return " ".join(tokens).strip()


def _walk_keys(value: object, prefix: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                errors.append(f"forbidden private field: {prefix}{key}")
            errors.extend(_walk_keys(nested, f"{prefix}{key}."))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            errors.extend(_walk_keys(nested, f"{prefix}{index}."))
    return errors


def _official_url(value: object) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def sequence_digest(dois: list[str]) -> str:
    joined = "\n".join(doi.strip().lower() for doi in dois)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def validate_evidence(evidence: dict[str, Any]) -> None:
    errors = _walk_keys(evidence)
    if evidence.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if evidence.get("capture_mode") != "official-roster-evidence":
        errors.append("capture_mode must be official-roster-evidence")
    if evidence.get("method") not in ALLOWED_METHODS:
        errors.append("method must be browser-authorized or official-page-read")
    if evidence.get("finalized") is not True:
        errors.append("finalized must be true")
    if "allow_archive_reorder" in evidence and not isinstance(
        evidence.get("allow_archive_reorder"), bool
    ):
        errors.append("allow_archive_reorder must be boolean")
    for field in ("captured_at", "journal_id", "issue_id", "official_url"):
        if not str(evidence.get(field, "")).strip():
            errors.append(f"{field} missing")
    if not _official_url(evidence.get("official_url")):
        errors.append("official_url must use an allowlisted publisher host")
    items = evidence.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items must be a non-empty array")
    else:
        seen: set[str] = set()
        for expected, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                errors.append(f"item {expected}: expected object")
                continue
            if item.get("sequence") != expected:
                errors.append(f"item {expected}: sequence must be contiguous")
            doi = str(item.get("doi", "")).strip().lower()
            if not DOI_RE.fullmatch(doi):
                errors.append(f"item {expected}: invalid DOI")
            elif doi in seen:
                errors.append(f"item {expected}: duplicate DOI {doi}")
            seen.add(doi)
            if not _normalized_title(item.get("title_en")):
                errors.append(f"item {expected}: title_en missing")
            detail_fields = ("authors", "abstract_en", "source_url")
            has_detail = any(item.get(field) for field in detail_fields)
            if has_detail:
                authors = item.get("authors")
                if not isinstance(authors, list) or not authors or not all(
                    str(author).strip() for author in authors
                ):
                    errors.append(f"item {expected}: authors must be a non-empty array")
                if not str(item.get("abstract_en", "")).strip():
                    errors.append(f"item {expected}: abstract_en missing")
                if not _official_url(item.get("source_url")):
                    errors.append(
                        f"item {expected}: source_url must use an allowlisted publisher host"
                    )
    excluded_items = evidence.get("excluded_items", [])
    if not isinstance(excluded_items, list):
        errors.append("excluded_items must be an array")
    else:
        for expected, item in enumerate(excluded_items, start=1):
            if not isinstance(item, dict):
                errors.append(f"excluded item {expected}: expected object")
                continue
            doi = str(item.get("doi", "")).strip().lower()
            if not DOI_RE.fullmatch(doi):
                errors.append(f"excluded item {expected}: invalid DOI")
            if not _normalized_title(item.get("title_en")):
                errors.append(f"excluded item {expected}: title_en missing")
            if not str(item.get("reason", "")).strip():
                errors.append(f"excluded item {expected}: reason missing")
    if (
        "excluded_items" in evidence
        and int(evidence.get("excluded_item_count", 0)) != len(excluded_items)
    ):
        errors.append("excluded_item_count does not match excluded_items")
    if errors:
        raise ValueError("official roster evidence gate failed:\n" + "\n".join(errors))


def _restored_article(
    item: dict[str, Any],
    *,
    sequence: int,
    official_url: str,
    publication_date: str,
) -> dict[str, Any]:
    missing = [
        field
        for field in ("authors", "abstract_en", "source_url")
        if not item.get(field)
    ]
    if missing:
        raise ValueError(
            f"official item {sequence} missing restoration detail: {', '.join(missing)}"
        )
    source_url = str(item["source_url"])
    if not _official_url(source_url):
        raise ValueError(
            f"official item {sequence} source_url must use an allowlisted publisher host"
        )
    doi = str(item["doi"]).strip().lower()
    article: dict[str, Any] = {
        "paper_id": f"doi:{doi}",
        "sequence": sequence,
        "source_sequence": sequence,
        "article_type": "research-article",
        "title_en": str(item["title_en"]).strip(),
        "title_cn": "",
        "authors": [str(author).strip() for author in item["authors"]],
        "abstract_en": str(item["abstract_en"]).strip(),
        "abstract_cn": "",
        "doi": doi,
        "source_url": source_url,
        "sources": {
            "roster": official_url,
            "metadata": source_url,
            "abstract_en": source_url,
        },
        "translation": {"status": "missing"},
        "quality_flags": ["title_cn_missing", "abstract_cn_missing"],
    }
    if publication_date:
        article["publication_date"] = publication_date
    return article


def apply_evidence(issue: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    validate_evidence(evidence)
    candidate = copy.deepcopy(issue)
    if str(candidate.get("journal_id", "")) != str(evidence["journal_id"]):
        raise ValueError("evidence journal_id does not match archive")
    if str(candidate.get("issue_id", "")) != str(evidence["issue_id"]):
        raise ValueError("evidence issue_id does not match archive")

    archive_articles = list(candidate.get("articles", []))
    evidence_items = list(evidence["items"])
    archive_dois = [str(article.get("doi", "")).strip().lower() for article in archive_articles]
    evidence_dois = [str(item["doi"]).strip().lower() for item in evidence_items]
    archive_by_doi = dict(zip(archive_dois, archive_articles, strict=True))
    evidence_positions = {doi: index for index, doi in enumerate(evidence_dois)}
    excluded_dois = {
        str(item.get("doi", "")).strip().lower()
        for item in evidence.get("excluded_items", [])
    }
    if any(
        doi not in evidence_positions and doi not in excluded_dois
        for doi in archive_dois
    ):
        raise ValueError("archive contains a DOI absent from the official roster")
    archive_positions = [
        evidence_positions[doi] for doi in archive_dois if doi in evidence_positions
    ]
    archive_reordered = archive_positions != sorted(archive_positions)
    if archive_reordered and evidence.get("allow_archive_reorder") is not True:
        raise ValueError("official DOI roster/order does not match archive")
    for article in archive_articles:
        doi = str(article.get("doi", "")).strip().lower()
        if doi in excluded_dois:
            continue
        item = evidence_items[evidence_positions[doi]]
        if _normalized_title(article.get("title_en")) != _normalized_title(
            item.get("title_en")
        ):
            raise ValueError(
                f"official title does not match archive at item {evidence_positions[doi] + 1}"
            )

    official_url = str(evidence["official_url"])
    publication_date = str(candidate.get("publication_date", ""))
    merged_articles: list[dict[str, Any]] = []
    restored_count = 0
    for sequence, item in enumerate(evidence_items, start=1):
        doi = str(item["doi"]).strip().lower()
        if doi in archive_by_doi:
            article = copy.deepcopy(archive_by_doi[doi])
            article["sequence"] = sequence
            article["source_sequence"] = sequence
        else:
            restored_count += 1
            article = _restored_article(
                item,
                sequence=sequence,
                official_url=official_url,
                publication_date=publication_date,
            )
        article.setdefault("sources", {})["roster"] = official_url
        merged_articles.append(article)
    candidate["articles"] = merged_articles
    candidate["expected_article_count"] = len(merged_articles)
    candidate["research_article_count"] = len(merged_articles)
    if restored_count or len(merged_articles) != len(archive_articles):
        candidate = normalize_issue_content(candidate)
    quality = candidate.setdefault("quality", {})
    quality["flags"] = [
        flag
        for flag in quality.get("flags", [])
        if flag
        not in {
            "crossref_provisional_roster",
            "publisher_html_blocked_crossref_fallback",
            "publisher_html_blocked_repec_fallback",
            "repec_publisher_supplied_roster",
            "official_order_unverified",
        }
    ]
    quality.update(
        roster_match=True,
        order_preserved=True,
        roster_authority="official-issue-page",
        roster_transport=str(evidence["method"]),
        official_item_count=len(evidence_items)
        + int(evidence.get("excluded_item_count", 0)),
        official_roster_evidence={
            "captured_at": str(evidence["captured_at"]),
            "official_url": official_url,
            "method": str(evidence["method"]),
            "item_count": len(evidence_items),
            "excluded_item_count": int(evidence.get("excluded_item_count", 0)),
            "archive_reordered": archive_reordered,
            "sequence_sha256": sequence_digest(evidence_dois),
            "privacy_fields_stored": False,
        },
    )
    candidate["source_url"] = official_url
    stamp_issue_readiness(candidate)
    validate_issue(candidate)
    return candidate


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def reconcile_state_files(candidate: dict[str, Any], state_root: Path) -> list[Path]:
    """Keep every checkpoint containing this issue aligned with archive truth."""

    updated: list[Path] = []
    issue_id = str(candidate["issue_id"])
    publication_state = str(candidate["publication_state"])
    retry_class = {
        "ready": "manual",
        "translation_partial": "translation",
        "source_pending": "source",
    }.get(publication_state, "manual")
    for path in sorted(state_root.glob("*.json")):
        payload = _read_json(path)
        issues = payload.get("issues")
        if not isinstance(issues, dict) or not isinstance(issues.get(issue_id), dict):
            continue
        entry = issues[issue_id]
        entry.update(
            status=publication_state,
            last_error="" if publication_state == "ready" else entry.get("last_error", ""),
            retry_class=retry_class,
            content_status=str(candidate["content_status"]),
            source_status=str(candidate["source_status"]),
            publication_state=publication_state,
            official_url=str(candidate["source_url"]),
        )
        entry.pop("next_retry_at", None)
        payload["updated_at"] = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        _write_json(path, payload)
        updated.append(path)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--api-root", type=Path, default=PUBLIC_API)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--translation-cache-root", type=Path, default=TRANSLATION_CACHE)
    args = parser.parse_args()

    evidence = _read_json(args.evidence)
    archive = (
        args.api_root
        / "journals"
        / str(evidence.get("journal_id", ""))
        / "issues"
        / f"{evidence.get('issue_id', '')}.json"
    )
    if not archive.exists():
        raise FileNotFoundError(f"archive missing: {archive}")
    candidate = apply_evidence(_read_json(archive), evidence)
    translation_report: dict[str, Any] | None = None
    if args.translate:
        cache_path = args.translation_cache_root / f"{candidate['journal_id']}.json"
        translation_report = translate_missing(candidate, cache_path)
        candidate = apply_translation_cache(candidate, cache_path=cache_path)
        candidate = normalize_issue_content(candidate)
        validate_issue(candidate)
    output = args.output or args.evidence.with_suffix(".candidate.json")
    _write_json(output, candidate)
    if args.promote:
        _write_json(archive, candidate)
        state_files = (
            reconcile_state_files(candidate, args.state_root)
            if args.state_root is not None
            else []
        )
        readback = _read_json(archive)
        if readback.get("publication_state") != candidate.get("publication_state"):
            raise RuntimeError("archive evidence promotion read-back failed")
    print(
        json.dumps(
            {
                "issue_id": candidate["issue_id"],
                "source_status": candidate["source_status"],
                "publication_state": candidate["publication_state"],
                "output": str(output),
                "promoted": bool(args.promote),
                "state_files_updated": len(state_files) if args.promote else 0,
                "translation": translation_report,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
