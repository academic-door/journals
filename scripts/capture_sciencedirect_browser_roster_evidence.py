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
)


PII_RE = re.compile(r"/pii/([A-Z0-9]+)", re.IGNORECASE)
ALLOWED_EXCLUDED_TYPES = {"editorial", "erratum"}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object")
    return payload


def _pii(value: object) -> str:
    match = PII_RE.search(str(value or ""))
    return match.group(1).upper() if match else ""


def _archive_by_pii(issue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for article in issue.get("articles", []):
        pii = _pii(article.get("source_url"))
        if not pii:
            raise ValueError(
                f"archive article lacks an official ScienceDirect PII: {article.get('doi')}"
            )
        if pii in mapped:
            raise ValueError(f"duplicate archive PII: {pii}")
        mapped[pii] = article
    return mapped


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

    archive_by_pii = _archive_by_pii(issue)
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for browser_item in snapshot.get("items", []):
        pii = _pii(browser_item.get("href"))
        title = str(browser_item.get("title", "")).strip()
        article_type = str(browser_item.get("type", "")).strip().casefold()
        if not pii or not title:
            raise ValueError("browser roster item is missing PII or title")
        if pii in seen:
            raise ValueError(f"duplicate official browser PII: {pii}")
        seen.add(pii)
        if article_type == "research-article":
            article = archive_by_pii.get(pii)
            if article is None:
                raise ValueError(f"official research PII is absent from archive: {pii}")
            if _normalized_title(title) != _normalized_title(article.get("title_en")):
                raise ValueError(f"official title mismatch for {pii}")
            items.append(
                {
                    "sequence": len(items) + 1,
                    "doi": str(article["doi"]).strip().casefold(),
                    "title_en": title,
                }
            )
            continue
        if article_type not in ALLOWED_EXCLUDED_TYPES:
            raise ValueError(f"unclassified official browser item {pii}: {article_type}")
        doi = str(excluded_dois.get(pii, "")).strip().casefold()
        if not doi:
            raise ValueError(f"excluded official item lacks DOI evidence: {pii}")
        excluded.append(
            {
                "doi": doi,
                "title_en": title,
                "reason": f"official-{article_type}",
            }
        )

    official_research_piis = {
        _pii(item.get("href"))
        for item in snapshot.get("items", [])
        if str(item.get("type", "")).casefold() == "research-article"
    }
    official_excluded_piis = {
        _pii(item.get("href"))
        for item in snapshot.get("items", [])
        if str(item.get("type", "")).casefold() in ALLOWED_EXCLUDED_TYPES
    }
    covered_archive_piis = official_research_piis | official_excluded_piis
    if not official_research_piis.issubset(archive_by_pii) or not set(
        archive_by_pii
    ).issubset(covered_archive_piis):
        missing = sorted(set(archive_by_pii) - covered_archive_piis)
        extra = sorted(official_research_piis - set(archive_by_pii))
        raise ValueError(f"official/archive PII set differs: missing={missing}, extra={extra}")

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
