"""Capture exact AEA issue rosters from the publisher's current archive URLs."""

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

from collectors.aea import NON_RESEARCH_PATTERN, USER_AGENT, _fetch_detail
from scripts.import_official_roster_evidence import apply_evidence


AEA_SLUGS = {
    "AEJAPP": "app",
    "AEJMACRO": "mac",
    "AEJMICRO": "mic",
    "AEJPOL": "pol",
    "AERI": "aeri",
    "JEP": "jep",
}
ISSUE_LINK_RE = re.compile(r"^/issues/(\d+)$")
VOLUME_ISSUE_RE = re.compile(
    r"Vol\.\s*([0-9]+),\s*No\.\s*([0-9]+)", re.IGNORECASE
)
DOI_RE = re.compile(r"^10\.1257/\S+$", re.IGNORECASE)


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _get(url: str, *, attempts: int = 4) -> requests.Response:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                timeout=45,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert error is not None
    raise error


def parse_archive_links(html: bytes | str) -> dict[tuple[str, str], str]:
    soup = BeautifulSoup(html, "html.parser")
    links: dict[tuple[str, str], str] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href", "")).strip()
        if not ISSUE_LINK_RE.fullmatch(href):
            continue
        match = VOLUME_ISSUE_RE.search(_text(anchor))
        if match:
            links[match.groups()] = urljoin("https://www.aeaweb.org", href)
    return links


def parse_issue_roster(
    html: bytes | str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for article in soup.select("article.journal-article"):
        doi = str(article.get("id", "")).strip().lower()
        if not DOI_RE.fullmatch(doi):
            continue
        title = _text(article.select_one("h3.title"))
        if not title:
            raise ValueError(f"official AEA item {doi} has no title")
        if NON_RESEARCH_PATTERN.search(title):
            excluded.append(
                {"doi": doi, "title_en": title, "reason": "non-research-title"}
            )
            continue
        if doi in seen:
            raise ValueError(f"duplicate official AEA DOI: {doi}")
        seen.add(doi)
        items.append(
            {"sequence": len(items) + 1, "doi": doi, "title_en": title}
        )
    if not items:
        raise ValueError("official AEA issue page contains no research articles")
    return items, excluded


def build_evidence(
    record: dict[str, Any],
    *,
    official_url: str,
    html: bytes | str,
    captured_at: str,
) -> dict[str, Any]:
    items, excluded = parse_issue_roster(html)
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
        default=ROOT / "data" / "provenance" / "official-rosters" / "aea",
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
        and record.get("journal") in AEA_SLUGS
        and not (
            args.skip_existing
            and (args.output_root / f"{record.get('issue_id', '')}.json").exists()
        )
    ]
    archive_maps: dict[str, dict[tuple[str, str], str]] = {}
    for journal in sorted({str(record["journal"]) for record in records}):
        slug = AEA_SLUGS[journal]
        archive_url = f"https://www.aeaweb.org/journals/{slug}/issues"
        archive_maps[journal] = parse_archive_links(_get(archive_url).content)

    jobs: list[tuple[dict[str, Any], str]] = []
    failures: list[dict[str, str]] = []
    for record in records:
        key = (str(record.get("volume", "")), str(record.get("issue", "")))
        official_url = archive_maps[str(record["journal"])].get(key, "")
        if not official_url:
            failures.append(
                {
                    "issue_id": str(record["issue_id"]),
                    "error": f"official Vol. {key[0]}, No. {key[1]} link not found",
                }
            )
            continue
        jobs.append((record, official_url))

    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    captured: list[str] = []
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
                response = future.result()
                evidence = build_evidence(
                    record,
                    official_url=official_url,
                    html=response.content,
                    captured_at=captured_at,
                )
                archive = (
                    args.api_root
                    / "journals"
                    / str(record["journal"]).casefold()
                    / "issues"
                    / f"{issue_id}.json"
                )
                archive_payload = json.loads(archive.read_text(encoding="utf-8"))
                archive_dois = {
                    str(article.get("doi", "")).strip().lower()
                    for article in archive_payload.get("articles", [])
                }
                for item in evidence["items"]:
                    if str(item["doi"]).lower() in archive_dois:
                        continue
                    detail = _fetch_detail(str(item["doi"]), int(item["sequence"]))
                    if not detail.get("authors") or not detail.get("abstract_en"):
                        raise ValueError(
                            f"official detail incomplete for {item['doi']}"
                        )
                    item.update(
                        authors=detail["authors"],
                        abstract_en=detail["abstract_en"],
                        source_url=detail["source_url"],
                    )
                apply_evidence(archive_payload, evidence)
                _write_json(args.output_root / f"{issue_id}.json", evidence)
                captured.append(issue_id)
            except Exception as exc:  # noqa: BLE001 - preserve per-issue evidence failure
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
