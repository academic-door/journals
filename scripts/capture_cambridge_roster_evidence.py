"""Capture official Cambridge issue rosters for source-pending archives."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_official_roster_evidence import validate_evidence


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
ISSUE_LABEL_RE = re.compile(
    r"Issue\s+(\d+(?:[-–]\d+)?)\s+[A-Za-z]+\s+(\d{4})"
)
DOI_DATA_RE = re.compile(r'data-doi="(10\.[^"]+)"')
NON_RESEARCH_RE = re.compile(
    r"^\s*(?:cover\s+and\s+(?:front|back)\s+matter|"
    r"editors?[^a-z]*report|editorial|correction|erratum|"
    r"corrigendum|retraction|expression\s+of\s+concern)\b",
    re.IGNORECASE,
)
BOOK_REVIEW_RE = re.compile(r"\bPp\.\s*\d+|\(Orgs?\.\)|\s+By\s+[A-Z]", re.IGNORECASE)


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _get(session: requests.Session, url: str, *, attempts: int = 3) -> requests.Response:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(
                url,
                timeout=60,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                },
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert error is not None
    raise error


def _normalize_issue(value: str) -> str:
    return str(value or "").strip().replace("–", "-")


def cambridge_all_issues_url(record: dict[str, Any]) -> str:
    official = str(record.get("official_url", "") or "").strip().rstrip("/")
    parsed = urlparse(official)
    if parsed.scheme != "https" or parsed.hostname != "www.cambridge.org":
        raise ValueError(f"{record.get('issue_id', '')}: not a Cambridge URL")
    return f"{official}/all-issues"


def parse_cambridge_all_issues(
    html: bytes | str,
    *,
    base_url: str,
) -> dict[tuple[str, str], str]:
    soup = BeautifulSoup(html, "html.parser")
    issues: dict[tuple[str, str], str] = {}
    for link in soup.select('a[href*="/issue/"]'):
        label = _text(link)
        match = ISSUE_LABEL_RE.search(label)
        if not match:
            continue
        key = (match.group(2), _normalize_issue(match.group(1)))
        issues.setdefault(key, urljoin(base_url, str(link.get("href", ""))))
    return issues


def cambridge_issue_url(
    record: dict[str, Any],
    issues: dict[tuple[str, str], str],
) -> str:
    year = str(record.get("year", "")).strip()
    issue = _normalize_issue(str(record.get("issue", "") or ""))
    url = issues.get((year, issue), "")
    if not url:
        raise ValueError(
            f"{record.get('issue_id', '')}: no Cambridge issue page for "
            f"year={year} issue={issue}"
        )
    return url


def _non_research_reason(title: str, authors: list[str], abstract: str) -> str:
    if NON_RESEARCH_RE.search(title):
        return "non-research-title"
    if not abstract and authors and BOOK_REVIEW_RE.search(title):
        return "book-review"
    if not abstract and not authors:
        return "front-matter-no-detail"
    return ""


def parse_cambridge_issue(
    html: bytes | str,
    *,
    base_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.select('a.part-link[href*="/article/"]'):
        title = _text(link)
        block = link.find_parent(
            "div", class_="product-listing-with-inputs-content"
        )
        if block is None:
            continue
        match = DOI_DATA_RE.search(str(block))
        if not match:
            continue
        doi = match.group(1).rstrip(".)\"").casefold()
        if doi in seen:
            raise ValueError(f"duplicate official Cambridge DOI: {doi}")
        seen.add(doi)
        authors = [
            _text(node)
            for node in block.select(".author .more-by-this-author")
            if _text(node)
        ]
        abstract_node = block.find("div", id=re.compile(r"^abstract"))
        abstract = _text(abstract_node) if abstract_node else ""
        source_url = urljoin(base_url, str(link.get("href", "")))
        reason = _non_research_reason(title, authors, abstract)
        if reason:
            excluded.append(
                {
                    "doi": doi,
                    "title_en": title,
                    "reason": reason,
                }
            )
            continue
        if not title or not abstract:
            raise ValueError(
                f"official Cambridge article detail is incomplete: {doi}"
            )
        items.append(
            {
                "sequence": len(items) + 1,
                "doi": doi,
                "title_en": title,
                "authors": authors,
                "abstract_en": abstract,
                "source_url": source_url,
            }
        )
    if not items:
        raise ValueError("official Cambridge issue page contains no research articles")
    return items, excluded


def build_evidence(
    record: dict[str, Any],
    *,
    official_url: str,
    items: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    captured_at: str,
) -> dict[str, Any]:
    evidence = {
        "schema_version": "1.0",
        "capture_mode": "official-roster-evidence",
        "method": "official-page-read",
        "captured_at": captured_at,
        "finalized": True,
        "journal_id": str(record["journal"]).casefold(),
        "issue_id": str(record["issue_id"]),
        "official_url": official_url,
        "excluded_item_count": len(excluded),
        "excluded_items": excluded,
        "items": items,
    }
    validate_evidence(evidence)
    return evidence


def select_cambridge_records(
    manifest: dict[str, Any],
    *,
    output_root: Path,
    skip_existing: bool,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in manifest.get("records", []):
        issue_id = str(record.get("issue_id", ""))
        if record.get("source_status") != "source_pending":
            continue
        if urlparse(str(record.get("official_url", ""))).hostname != "www.cambridge.org":
            continue
        if skip_existing and (output_root / f"{issue_id}.json").exists():
            continue
        records.append(record)
    return records


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def capture_record(
    record: dict[str, Any],
    *,
    captured_at: str,
) -> dict[str, Any]:
    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        all_issues_url = cambridge_all_issues_url(record)
        all_issues = parse_cambridge_all_issues(
            _get(session, all_issues_url).content,
            base_url=all_issues_url,
        )
        issue_url = cambridge_issue_url(record, all_issues)
        items, excluded = parse_cambridge_issue(
            _get(session, issue_url).content,
            base_url=issue_url,
        )
    return build_evidence(
        record,
        official_url=issue_url,
        items=items,
        excluded=excluded,
        captured_at=captured_at,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap-manifest", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data" / "provenance" / "official-rosters" / "cambridge",
    )
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--diagnostics-root", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.gap_manifest.read_text(encoding="utf-8"))
    records = select_cambridge_records(
        manifest,
        output_root=args.output_root,
        skip_existing=args.skip_existing,
    )
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    captured: list[str] = []
    failures: list[dict[str, str]] = []
    for record in records:
        issue_id = str(record["issue_id"])
        try:
            evidence = capture_record(record, captured_at=captured_at)
            _write_json(args.output_root / f"{issue_id}.json", evidence)
            captured.append(issue_id)
        except Exception as error:
            failures.append(
                {
                    "issue_id": issue_id,
                    "official_url": str(record.get("official_url", "")),
                    "error": str(error),
                }
            )
    if args.diagnostics_root and failures:
        _write_json(
            args.diagnostics_root / "cambridge-roster-failures.json",
            {
                "schema_version": "1.0",
                "captured_at": captured_at,
                "failures": failures,
            },
        )
    print(
        json.dumps(
            {"captured": sorted(captured), "failures": failures},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

