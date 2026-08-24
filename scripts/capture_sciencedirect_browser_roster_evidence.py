"""Convert authorized ScienceDirect browser rosters into official evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_official_roster_evidence import (  # noqa: E402
    _normalized_title,
    apply_evidence,
    validate_evidence,
)


PII_RE = re.compile(r"/pii/([A-Z0-9]+)", re.IGNORECASE)
ALLOWED_EXCLUDED_TYPES = {"editorial", "erratum"}
NON_RESEARCH_RE = re.compile(
    r"\bEditorial(?:\s+Board)?\b|\bErratum\b|\bCorrigendum\b|"
    r"\bCorrection\b|\bRetraction\b|\bFront\s+matter\b|"
    r"\bIntroduction\s+to\s+(?:the\s+)?special\s+issue\b",
    re.IGNORECASE,
)
PUBLISHABLE_RE = re.compile(
    r"Research article|Review article|Short communication|"
    r"Full length article|Data article|Discussion",
    re.IGNORECASE,
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object")
    return payload


def _pii(value: object) -> str:
    match = PII_RE.search(str(value or ""))
    return match.group(1).upper() if match else ""


def _archive_maps(
    issue: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_pii: dict[str, dict[str, Any]] = {}
    by_doi: dict[str, dict[str, Any]] = {}
    for article in issue.get("articles", []):
        pii = _pii(article.get("source_url"))
        doi = str(article.get("doi", "")).strip().casefold()
        if not doi:
            raise ValueError("archive article lacks DOI")
        if doi in by_doi:
            raise ValueError(f"duplicate archive DOI: {doi}")
        by_doi[doi] = article
        if pii in by_pii:
            raise ValueError(f"duplicate archive PII: {pii}")
        if pii:
            by_pii[pii] = article
    return by_pii, by_doi


def _article_type(browser_item: dict[str, Any]) -> str:
    explicit = str(browser_item.get("type", "")).strip().casefold()
    if explicit in ALLOWED_EXCLUDED_TYPES or explicit == "research-article":
        return explicit
    box_text = str(browser_item.get("box_text", ""))
    if NON_RESEARCH_RE.search(box_text):
        return "erratum" if re.search(
            r"Erratum|Corrigendum|Correction|Retraction", box_text, re.IGNORECASE
        ) else "editorial"
    if PUBLISHABLE_RE.search(box_text):
        return "research-article"
    return ""


def build_evidence(
    snapshot: dict[str, Any],
    issue: dict[str, Any],
    *,
    excluded_dois: dict[str, str],
) -> dict[str, Any]:
    official_url = str(snapshot.get("official_url", ""))
    parsed = urlparse(official_url)
    if parsed.scheme != "https" or parsed.hostname != "www.sciencedirect.com":
        raise ValueError("snapshot official_url must be a ScienceDirect HTTPS URL")
    if str(snapshot.get("journal_id", "")) != str(issue.get("journal_id", "")):
        raise ValueError("snapshot journal_id does not match archive")
    if str(snapshot.get("issue_id", "")) != str(issue.get("issue_id", "")):
        raise ValueError("snapshot issue_id does not match archive")

    archive_by_pii, archive_by_doi = _archive_maps(issue)
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    matched_archive_dois: set[str] = set()
    missing_official_count = 0
    for browser_item in snapshot.get("items", []):
        pii = _pii(browser_item.get("href"))
        title = str(browser_item.get("title", "")).strip()
        browser_doi = str(browser_item.get("doi", "")).strip().casefold()
        article_type = _article_type(browser_item)
        if not pii or not title:
            raise ValueError("browser roster item is missing PII or title")
        if pii in seen:
            raise ValueError(f"duplicate official browser PII: {pii}")
        seen.add(pii)
        article = archive_by_pii.get(pii) or archive_by_doi.get(browser_doi)
        if article is not None:
            matched_archive_dois.add(str(article["doi"]).strip().casefold())
        if article_type == "research-article":
            if article is not None:
                doi = str(article["doi"]).strip().casefold()
            else:
                missing_official_count += 1
                doi = browser_doi
                authors = browser_item.get("authors")
                if not doi or not isinstance(authors, list) or not authors:
                    raise ValueError(
                        f"missing official research item lacks DOI/authors: {pii}"
                    )
            if article is not None and _normalized_title(title) != _normalized_title(
                article.get("title_en")
            ):
                raise ValueError(f"official title mismatch for {pii}")
            evidence_item: dict[str, Any] = {
                "sequence": len(items) + 1,
                "doi": doi,
                "title_en": title,
            }
            if article is None:
                evidence_item.update(
                    source_id=f"pii:{pii}",
                    official_authors=[str(author).strip() for author in authors],
                    official_article_url=(
                        f"https://www.sciencedirect.com/science/article/pii/{pii}"
                    ),
                )
            items.append(evidence_item)
            continue
        if article_type not in ALLOWED_EXCLUDED_TYPES:
            raise ValueError(f"unclassified official browser item {pii}")
        doi = (
            str(article["doi"]).strip().casefold()
            if article is not None
            else str(excluded_dois.get(pii, "")).strip().casefold()
        )
        excluded_item = {
            "title_en": title,
            "reason": f"official-{article_type}",
        }
        if doi:
            excluded_item["doi"] = doi
        else:
            excluded_item["source_id"] = f"pii:{pii}"
        excluded.append(excluded_item)

    archive_dois = set(archive_by_doi)
    if matched_archive_dois != archive_dois:
        missing = sorted(archive_dois - matched_archive_dois)
        raise ValueError(f"archive items absent from official browser roster: {missing}")

    evidence = {
        "schema_version": "1.0",
        "capture_mode": "official-roster-evidence",
        "method": "browser-authorized",
        "captured_at": str(snapshot.get("captured_at", "")),
        "finalized": True,
        "journal_id": str(issue["journal_id"]),
        "issue_id": str(issue["issue_id"]),
        "official_url": official_url,
        "allow_archive_reorder": True,
        "excluded_item_count": len(excluded),
        "excluded_items": excluded,
        "items": items,
    }
    validate_evidence(evidence)
    if missing_official_count == 0:
        apply_evidence(issue, evidence)
    return evidence


def _parse_excluded_dois(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        pii, separator, doi = value.partition("=")
        if not separator or not pii.strip() or not doi.strip():
            raise ValueError(f"invalid --excluded-doi mapping: {value}")
        parsed[pii.strip().upper()] = doi.strip().casefold()
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", nargs="+", type=Path)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--excluded-doi", action="append", default=[])
    args = parser.parse_args()
    excluded_dois = _parse_excluded_dois(args.excluded_doi)
    written: list[str] = []
    for snapshot_path in args.snapshots:
        snapshot = _read_json(snapshot_path)
        issue_id = str(snapshot["issue_id"])
        journal_id = str(snapshot["journal_id"])
        archive_path = (
            args.api_root / "journals" / journal_id / "issues" / f"{issue_id}.json"
        )
        evidence = build_evidence(
            snapshot,
            _read_json(archive_path),
            excluded_dois=excluded_dois,
        )
        output = args.output_root / f"{issue_id}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(issue_id)
    print(json.dumps({"written": written}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
