"""Capture official Springer issue rosters for historical recovery."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_official_roster_evidence import validate_evidence


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
NON_RESEARCH_RE = re.compile(
    r"^\s*(?:Correction|Erratum|Corrigendum|Retraction)\b|"
    r"^\s*Correction\s+to:|^\s*Editor's\s+Note\b|"
    r"^\s*Editorial\b|^\s*Publisher(?:'s)?\s+Correction\b",
    re.IGNORECASE,
)


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _clean_doi(value: object) -> str:
    match = DOI_RE.search(unquote(str(value or "")))
    return match.group(0).rstrip(".,);").casefold() if match else ""


def _get(session: requests.Session, url: str, *, attempts: int = 2) -> requests.Response:
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


def springer_issue_url(record: dict[str, Any]) -> str:
    official = str(record.get("official_url", "") or "")
    volume = str(record.get("volume", "") or "")
    issue = str(record.get("issue", "") or "")
    if not volume or not issue:
        raise ValueError(f"{record.get('issue_id', '')}: volume/issue missing")
    parsed = urlparse(official)
    if parsed.scheme == "https" and parsed.hostname == "link.springer.com":
        base = official.rstrip("/")
        if base.endswith(f"/{volume}-{issue}"):
            return base
        if base.endswith("/volumes-and-issues"):
            return f"{base}/{volume}-{issue}"
    journal_code = str(record.get("springer_journal_code", "") or "").strip()
    if journal_code:
        return (
            f"https://link.springer.com/journal/{journal_code}"
            f"/volumes-and-issues/{volume}-{issue}"
        )
    raise ValueError(f"{record.get('issue_id', '')}: cannot build Springer issue URL")


def springer_issue_url_candidates(record: dict[str, Any]) -> list[str]:
    primary = springer_issue_url(record)
    urls = [primary]
    issn = str(record.get("issn", "") or "").strip()
    volume = str(record.get("volume", "") or "").strip()
    issue = str(record.get("issue", "") or "").strip()
    if issn and volume and issue:
        urls.append(
            "https://link.springer.com/openurl"
            f"?genre=journal&issn={issn}&volume={volume}&issue={issue}"
        )
    return urls


def parse_springer_issue_links(
    html: bytes | str,
    *,
    base_url: str,
) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in soup.select('a[href*="/article/10."]'):
        href = str(link.get("href", "")).strip()
        doi = _clean_doi(href)
        title = _text(link)
        if not doi or not title or doi in seen:
            continue
        seen.add(doi)
        links.append(
            {
                "doi": doi,
                "title_en": title,
                "source_url": urljoin(base_url, href),
            }
        )
    if not links:
        raise ValueError("official Springer issue page contains no article links")
    return links


def parse_springer_detail(
    html: bytes | str,
    *,
    source_url: str,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    doi = ""
    doi_meta = soup.select_one('meta[name="citation_doi"][content]')
    if doi_meta is not None:
        doi = _clean_doi(doi_meta.get("content"))
    if not doi:
        doi = _clean_doi(source_url)

    title = ""
    title_meta = soup.select_one('meta[name="citation_title"][content]')
    if title_meta is not None:
        title = str(title_meta.get("content", "")).strip()
    if not title:
        title = _text(soup.select_one("h1"))

    authors = [
        str(node.get("content", "")).strip()
        for node in soup.select('meta[name="citation_author"][content]')
        if str(node.get("content", "")).strip()
    ]
    if not authors:
        authors = [
            _text(node)
            for node in soup.select(
                "[data-test='author-name'], "
                ".c-article-author-list__item, "
                ".c-article-author-list a"
            )
            if _text(node)
        ]

    abstract = ""
    abstract_meta = soup.select_one('meta[name="dc.description"][content]')
    if abstract_meta is not None:
        abstract = str(abstract_meta.get("content", "")).strip()
    if not abstract:
        for selector in (
            "section#Abs1",
            "section[data-title='Abstract']",
            ".c-article-section__content",
            "[id='Abs1-content']",
        ):
            abstract = _text(soup.select_one(selector))
            if abstract:
                break
    abstract = re.sub(r"^\s*Abstract\s*", "", abstract, flags=re.IGNORECASE).strip()

    if not doi or not title or not authors or not abstract:
        raise ValueError("official Springer article detail is incomplete")
    return {
        "doi": doi,
        "title_en": title,
        "authors": authors,
        "abstract_en": abstract,
        "source_url": source_url,
    }


def _non_research_reason(title: str) -> str:
    return "non-research-title" if NON_RESEARCH_RE.search(title) else ""


def build_evidence(
    record: dict[str, Any],
    *,
    official_url: str,
    details: list[dict[str, Any]],
    captured_at: str,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for detail in details:
        title = str(detail["title_en"]).strip()
        reason = _non_research_reason(title)
        if reason:
            excluded.append(
                {
                    "doi": str(detail["doi"]).strip().casefold(),
                    "title_en": title,
                    "reason": reason,
                }
            )
            continue
        items.append(
            {
                "sequence": len(items) + 1,
                "doi": str(detail["doi"]).strip().casefold(),
                "title_en": title,
                "authors": [str(author).strip() for author in detail["authors"]],
                "abstract_en": str(detail["abstract_en"]).strip(),
                "source_url": str(detail["source_url"]).strip(),
            }
        )
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


def select_springer_records(
    manifest: dict[str, Any],
    *,
    output_root: Path,
    skip_existing: bool,
    issue_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in manifest.get("records", []):
        issue_id = str(record.get("issue_id", ""))
        if issue_ids and issue_id.casefold() not in issue_ids:
            continue
        if record.get("source_status") != "source_pending":
            continue
        if urlparse(str(record.get("official_url", ""))).hostname != "link.springer.com":
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
    detail_workers: int = 6,
) -> dict[str, Any]:
    issue_url = ""
    links: list[dict[str, str]] = []
    errors: list[str] = []
    with requests.Session() as issue_session:
        issue_session.headers.update({"User-Agent": USER_AGENT})
        for candidate_url in springer_issue_url_candidates(record):
            issue_url = candidate_url
            try:
                issue_html = _get(issue_session, issue_url).content
                links = parse_springer_issue_links(issue_html, base_url=issue_url)
                break
            except Exception as error:
                errors.append(f"{candidate_url}: {error}")
    if not links:
        raise ValueError("; ".join(errors))
    details_by_doi: dict[str, dict[str, Any]] = {}

    def fetch_detail(link: dict[str, str]) -> tuple[str, dict[str, Any]]:
        with requests.Session() as detail_session:
            detail_session.headers.update({"User-Agent": USER_AGENT})
            detail = parse_springer_detail(
                _get(detail_session, link["source_url"]).content,
                source_url=link["source_url"],
            )
        if detail["doi"] != link["doi"]:
            raise ValueError(
                f"{record['issue_id']}: detail DOI mismatch for {link['source_url']}"
            )
        return link["doi"], detail

    with ThreadPoolExecutor(max_workers=max(1, detail_workers)) as pool:
        futures = {pool.submit(fetch_detail, link): link for link in links}
        for future in as_completed(futures):
            doi, detail = future.result()
            details_by_doi[doi] = detail
    details = [details_by_doi[link["doi"]] for link in links]
    return build_evidence(
        record,
        official_url=issue_url,
        details=details,
        captured_at=captured_at,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap-manifest", type=Path, required=True)
    parser.add_argument("--journals-config", type=Path, default=ROOT / "config" / "journals.yml")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data" / "provenance" / "official-rosters" / "springer",
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--detail-workers", type=int, default=6)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--issue-ids", default="")
    parser.add_argument("--diagnostics-root", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.gap_manifest.read_text(encoding="utf-8"))
    journals = yaml.safe_load(args.journals_config.read_text(encoding="utf-8"))[
        "journals"
    ]
    records = select_springer_records(
        manifest,
        output_root=args.output_root,
        skip_existing=args.skip_existing,
        issue_ids={
            value.strip().casefold()
            for value in args.issue_ids.split(",")
            if value.strip()
        }
        or None,
    )
    for record in records:
        journal = journals.get(str(record.get("journal", "")).upper(), {})
        if journal.get("issn"):
            record["issn"] = journal["issn"]
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    captured: list[str] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                capture_record,
                record,
                captured_at=captured_at,
                detail_workers=args.detail_workers,
            ): record
            for record in records
        }
        for future in as_completed(futures):
            record = futures[future]
            issue_id = str(record["issue_id"])
            try:
                evidence = future.result()
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
            args.diagnostics_root / "springer-roster-failures.json",
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
