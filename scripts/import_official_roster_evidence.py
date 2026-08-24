"""Promote provisional history archives after exact official-roster matching.

This importer is intentionally roster-only: it preserves the already audited
metadata and translations, and changes source authority only when an official
issue-page evidence file has the exact same DOI order and titles.
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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_journals import (
    PUBLIC_API,
    stamp_issue_readiness,
    validate_issue,
)


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
    return " ".join(text.split()).strip().casefold()


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
    if errors:
        raise ValueError("official roster evidence gate failed:\n" + "\n".join(errors))


def apply_evidence(issue: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    validate_evidence(evidence)
    candidate = copy.deepcopy(issue)
    if str(candidate.get("journal_id", "")) != str(evidence["journal_id"]):
        raise ValueError("evidence journal_id does not match archive")
    if str(candidate.get("issue_id", "")) != str(evidence["issue_id"]):
        raise ValueError("evidence issue_id does not match archive")

    archive_articles = list(candidate.get("articles", []))
    evidence_items = list(evidence["items"])
    archive_dois = [
        str(article.get("doi", "")).strip().lower() for article in archive_articles
    ]
    evidence_dois = [str(item["doi"]).strip().lower() for item in evidence_items]
    if archive_dois != evidence_dois:
        raise ValueError("official DOI roster/order does not match archive")
    for index, (article, item) in enumerate(
        zip(archive_articles, evidence_items, strict=True), start=1
    ):
        if _normalized_title(article.get("title_en")) != _normalized_title(
            item.get("title_en")
        ):
            raise ValueError(f"official title does not match archive at item {index}")

    official_url = str(evidence["official_url"])
    quality = candidate.setdefault("quality", {})
    quality["flags"] = [
        flag
        for flag in quality.get("flags", [])
        if flag
        not in {
            "crossref_provisional_roster",
            "publisher_html_blocked_crossref_fallback",
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
            "sequence_sha256": sequence_digest(evidence_dois),
            "privacy_fields_stored": False,
        },
    )
    candidate["source_url"] = official_url
    for article in archive_articles:
        article.setdefault("sources", {})["roster"] = official_url
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--api-root", type=Path, default=PUBLIC_API)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--promote", action="store_true")
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
    output = args.output or args.evidence.with_suffix(".candidate.json")
    _write_json(output, candidate)
    if args.promote:
        _write_json(archive, candidate)
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
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
