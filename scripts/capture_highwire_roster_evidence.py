"""Capture official HighWire issue rosters for source-pending archives."""

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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_official_roster_evidence import apply_evidence


USER_AGENT = (
    "AcademicDoorJournals/0.1 "
    "(non-profit academic metadata service; https://academic-door.github.io/)"
)
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;]+", re.IGNORECASE)
NON_RESEARCH_RE = re.compile(
    r"\bfront\s+matter\b|\bback\s+matter\b|\berratum\b|"
    r"\bcorrection\b|^editorial\b|^editor[’']?s\s+note\b|"
    r"^introduction\s+to\s+(?:the\s+)?special\s+issue\b",
    re.IGNORECASE,
)
NON_RESEARCH_SECTION_RE = re.compile(
    r"\berrat(?:um|a)\b|\bcorrection(?:s)?\b|\beditorial(?:s)?\b",
    re.IGNORECASE,
)


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _get(url: str, *, attempts: int = 4) -> requests.Response:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(
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


def _non_research_reason(card: Any, title: str) -> str:
    if NON_RESEARCH_RE.search(title):
        return "non-research-title"
    for heading in card.find_all_previous(["h2", "h3", "h4"], limit=10):
        classes = set(heading.get("class", []))
        if "toc-heading" not in classes:
            continue
        section = _text(heading)
        if NON_RESEARCH_SECTION_RE.search(section):
            return f"non-research-section:{section}"
        break
    return ""


def parse_highwire_roster(
    html: bytes | str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select(".highwire-cite-highwire-article"):
        title = _text(card.select_one(".highwire-cite-title"))
        metadata = _text(card.select_one(".highwire-cite-metadata-doi"))
        match = DOI_RE.search(metadata)
        if not title or not match:
            continue
        doi = match.group(0).rstrip(".)").casefold()
        if doi in seen:
            raise ValueError(f"duplicate official HighWire DOI: {doi}")
        seen.add(doi)
        exclusion_reason = _non_research_reason(card, title)
        if exclusion_reason:
            excluded.append(
                {"doi": doi, "title_en": title, "reason": exclusion_reason}
            )
            continue
        items.append(
            {"sequence": len(items) + 1, "doi": doi, "title_en": title}
        )
    if not items:
        raise ValueError("official HighWire issue page contains no research articles")
    return items, excluded


def parse_highwire_detail_urls(html: bytes | str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: dict[str, str] = {}
    for card in soup.select(".highwire-cite-highwire-article"):
        metadata = _text(card.select_one(".highwire-cite-metadata-doi"))
        match = DOI_RE.search(metadata)
        link = card.select_one("a.highwire-cite-linked-title[href]")
        if match and link is not None:
            urls[match.group(0).rstrip(".)").casefold()] = urljoin(
                "https://le.uwpress.org", str(link.get("href", ""))
            )
    return urls


def parse_highwire_detail(
    html: bytes | str,
    *,
    source_url: str,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    authors = [
        str(node.get("content", "")).strip()
        for node in soup.select('meta[name="citation_author"][content]')
        if str(node.get("content", "")).strip()
    ]
    if not authors:
        authors = [
            _text(node)
            for node in soup.select(".highwire-citation-author")
            if _text(node)
        ]
    abstract = ""
    for selector in (
        ".abstract",
        "section.abstract",
        ".section.abstract",
        "[class*='abstract']",
    ):
        abstract = _text(soup.select_one(selector))
        if abstract:
            break
    if not abstract:
        node = soup.select_one('meta[name="citation_abstract"][content]')
        abstract = str(node.get("content", "")).strip() if node else ""
    abstract = re.sub(r"^Abstract\s*", "", abstract, flags=re.IGNORECASE)
    if not authors or not abstract:
        raise ValueError("official HighWire article detail is incomplete")
    return {"authors": authors, "abstract_en": abstract, "source_url": source_url}


def build_evidence(
    record: dict[str, Any],
    *,
    official_url: str,
    html: bytes | str,
    captured_at: str,
) -> dict[str, Any]:
    items, excluded = parse_highwire_roster(html)
    return {
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap-manifest", type=Path, required=True)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data" / "provenance" / "official-rosters" / "landecon",
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--diagnostics-root", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.gap_manifest.read_text(encoding="utf-8"))
    records = [
        record
        for record in manifest.get("records", [])
        if record.get("category") == "source_pending"
        and record.get("journal") == "LANDECON"
        and not (
            args.skip_existing
            and (args.output_root / f"{record.get('issue_id', '')}.json").exists()
        )
    ]
    jobs = [
        (
            record,
            f"https://le.uwpress.org/content/{record['volume']}/{record['issue']}",
        )
        for record in records
    ]
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    captured: list[str] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(_get, official_url): (record, official_url)
            for record, official_url in jobs
        }
        for future in as_completed(futures):
            record, official_url = futures[future]
            issue_id = str(record["issue_id"])
            evidence: dict[str, Any] | None = None
            try:
                content = future.result().content
                evidence = build_evidence(
                    record,
                    official_url=official_url,
                    html=content,
                    captured_at=captured_at,
                )
                archive = (
                    args.api_root
                    / "journals"
                    / "landecon"
                    / "issues"
                    / f"{issue_id}.json"
                )
                archive_payload = json.loads(archive.read_text(encoding="utf-8"))
                archive_dois = {
                    str(article.get("doi", "")).strip().casefold()
                    for article in archive_payload.get("articles", [])
                }
                detail_urls = parse_highwire_detail_urls(content)
                for item in evidence["items"]:
                    doi = str(item["doi"]).casefold()
                    if doi in archive_dois:
                        continue
                    detail_url = detail_urls.get(doi, "")
                    if not detail_url:
                        raise ValueError(f"official detail URL missing for {doi}")
                    item.update(
                        parse_highwire_detail(
                            _get(detail_url).content,
                            source_url=detail_url,
                        )
                    )
                apply_evidence(archive_payload, evidence)
                _write_json(args.output_root / f"{issue_id}.json", evidence)
                captured.append(issue_id)
            except Exception as exc:  # noqa: BLE001 - retain per-issue failure
                if evidence is not None and args.diagnostics_root is not None:
                    _write_json(args.diagnostics_root / f"{issue_id}.json", evidence)
                failures.append({"issue_id": issue_id, "error": str(exc)})

    print(
        json.dumps(
            {
                "selected": len(records),
                "captured": len(captured),
                "failed": sorted(failures, key=lambda item: item["issue_id"]),
                "captured_issue_ids": sorted(captured),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
