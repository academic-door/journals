"""Build historical archives from authorized ScienceDirect rosters and Elsevier metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.metadata_fallback import (
    ELSEVIER_SEARCH_API,
    _elsevier_lookup,
)
from scripts.import_browser_authorized_snapshot import (
    build_candidate,
    now_iso,
    translate_candidate,
    write_json,
)
from scripts.import_official_roster_evidence import reconcile_state_files
from scripts.update_journals import (
    TRANSLATION_CACHE,
    archive_issue,
    is_archivable_snapshot,
    public_issue_path,
    validate_issue,
)

PII_RE = re.compile(r"/pii/([A-Z0-9]+)", re.IGNORECASE)
PUBLISHABLE_RE = re.compile(
    r"Research article|Review article|Short communication|"
    r"Full length article|Data article|Discussion",
    re.IGNORECASE,
)
VOLUME_RE = re.compile(r"/vol/([^/]+)", re.IGNORECASE)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def pii_from_href(value: object) -> str:
    match = PII_RE.search(str(value or ""))
    return match.group(1).upper() if match else ""


def raw_type(box_text: str) -> str:
    match = re.search(
        r"(Research article|Review article|Short communication|Full length article|"
        r"Data article|Discussion|Editorial|Erratum|Corrigendum|Correction)",
        box_text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return "Research article" if PUBLISHABLE_RE.search(box_text) else "Editorial"


def volume_from_roster(roster: dict[str, Any]) -> str:
    explicit = str(roster.get("volume", "")).strip()
    if explicit:
        return explicit
    match = VOLUME_RE.search(str(roster.get("official_url", "")))
    if match:
        return match.group(1)
    issue_id = str(roster.get("issue_id", ""))
    parts = issue_id.split("-")
    if len(parts) >= 3 and parts[-2]:
        return parts[-2]
    raise ValueError(f"cannot determine volume for {issue_id}")


def fetch_article_metadata(
    session: requests.Session,
    pii: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/xml",
        "X-ELS-APIKey": os.getenv("ELSEVIER_API_KEY", "").strip(),
    }
    inst_token = os.getenv("ELSEVIER_INST_TOKEN", "").strip()
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    response = session.get(
        ELSEVIER_SEARCH_API,
        params={
            "query": f'PII("{pii}")',
            "field": "url,identifier,doi,pii,title,creator,description,coverDate",
            "count": "5",
            "httpAccept": "application/xml",
        },
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "dc": "http://purl.org/dc/elements/1.1/",
        "prism": "http://prismstandard.org/namespaces/basic/2.0/",
        "sci": "http://www.elsevier.com/xml/schemas/sciencedirect",
    }
    for entry in root.findall("atom:entry", ns):
        entry_pii = re.sub(
            r"[^A-Za-z0-9]",
            "",
            entry.findtext("pii", "", ns).strip()
            or entry.findtext("sci:pii", "", ns).strip(),
        ).upper()
        if entry_pii != pii.upper():
            continue
        doi = entry.findtext("prism:doi", "", ns).strip().lower()
        title = entry.findtext("dc:title", "", ns).strip()
        authors = [
            node.text.strip()
            for node in entry.findall("dc:creator", ns)
            if node.text and node.text.strip()
        ]
        abstract = entry.findtext("dc:description", "", ns).strip()
        if not abstract:
            lookup = _elsevier_lookup(session, pii, doi=doi, timeout=timeout)
            abstract = str(lookup.get("abstract", "")).strip()
        return {
            "doi": doi,
            "title_en": title,
            "authors": authors,
            "abstract_en": abstract,
        }
    raise ValueError(f"Elsevier metadata returned no article for PII {pii}")


def build_rich_snapshot(
    roster: dict[str, Any],
    *,
    session: requests.Session,
    journal: dict[str, Any],
) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    roster_items = list(roster.get("items", []))
    with ThreadPoolExecutor(max_workers=6) as pool:
        metadata_items = list(
            pool.map(
                lambda item: fetch_article_metadata(
                    session,
                    pii_from_href(item.get("href")),
                    timeout=90,
                ),
                roster_items,
            )
        )
    by_pii = {
        pii_from_href(item.get("href")): metadata
        for item, metadata in zip(roster_items, metadata_items, strict=True)
    }

    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for order, item in enumerate(roster_items, start=1):
        pii = pii_from_href(item.get("href"))
        metadata = by_pii.get(pii)
        if metadata is None:
            missing.append(pii or str(item.get("title", "")))
            continue
        title = str(item.get("title", "")).strip()
        api_title = str(metadata.get("title_en", "")).strip()
        if title.casefold() != api_title.casefold():
            raise ValueError(f"official title mismatch for {pii}: {title!r} != {api_title!r}")
        items.append(
            {
                "official_order": order,
                "pii": pii,
                "doi": str(metadata.get("doi", "")).strip().lower(),
                "title_en": title,
                "raw_type": raw_type(str(item.get("box_text", ""))),
                "authors": list(metadata.get("authors", [])),
                "abstract_en": str(metadata.get("abstract_en", "")).strip(),
                "source_url": f"https://www.sciencedirect.com/science/article/pii/{pii}",
            }
        )
    if missing:
        raise ValueError(f"Elsevier metadata missing roster PIIs: {missing}")
    if len(items) != len(roster.get("items", [])):
        raise ValueError("browser roster and official metadata counts differ")
    return {
        "schema_version": "1.0",
        "journal_id": str(roster["journal_id"]),
        "journal_name": str(journal["name"]),
        "volume": volume_from_roster(roster),
        "issue": str(roster.get("issue", "c")),
        "publication_date": str(roster["publication_date"]),
        "source_url": str(roster["official_url"]),
        "captured_at": str(roster.get("captured_at") or now_iso()),
        "capture_mode": "browser-authorized",
        "institutional_access_confirmed": True,
        "items": items,
    }


def promote_historical(candidate: dict[str, Any], *, state_root: Path) -> Path:
    if not is_archivable_snapshot(candidate):
        raise ValueError(
            f"publication gate failed for {candidate['issue_id']}: "
            f"{candidate['quality'].get('translation_complete', 0)}/"
            f"{candidate['research_article_count']} translated"
        )
    target = public_issue_path(str(candidate["journal_id"]))
    target = target.parent / "issues" / f"{candidate['issue_id']}.json"
    write_json(target, candidate)
    archive_issue(candidate)
    reconcile_state_files(candidate, state_root)
    readback = read_json(target)
    if readback.get("issue_id") != candidate.get("issue_id"):
        raise RuntimeError(f"historical archive read-back failed: {target}")
    return target


def load_journal(journal_id: str) -> dict[str, Any]:
    config = yaml.safe_load((ROOT / "config" / "journals.yml").read_text(encoding="utf-8"))
    for journal in config["journals"].values():
        if str(journal.get("id")) == journal_id:
            return journal
    raise KeyError(f"journal not configured: {journal_id}")


def process(path: Path, *, state_root: Path, cache_root: Path, translate: bool) -> dict[str, Any]:
    roster = read_json(path)
    journal = load_journal(str(roster["journal_id"]))
    session = requests.Session()
    snapshot = build_rich_snapshot(roster, session=session, journal=journal)
    candidate = build_candidate(snapshot)
    translation_report: dict[str, Any] = {}
    if translate:
        candidate, translation_report = translate_candidate(
            candidate,
            cache_root / f"{candidate['journal_id']}.json",
            session=session,
        )
    validate_issue(candidate)
    target = ""
    if candidate.get("publication_state") == "ready":
        target = str(promote_historical(candidate, state_root=state_root))
    return {
        "issue_id": candidate["issue_id"],
        "publication_state": candidate.get("publication_state"),
        "content_status": candidate.get("content_status"),
        "source_status": candidate.get("source_status"),
        "target": target,
        "translation": translation_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshots", nargs="+", type=Path)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--translation-cache-root", type=Path, default=TRANSLATION_CACHE)
    parser.add_argument("--translate", action="store_true")
    args = parser.parse_args()
    results = [
        process(
            path,
            state_root=args.state_root,
            cache_root=args.translation_cache_root,
            translate=args.translate,
        )
        for path in args.snapshots
    ]
    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
