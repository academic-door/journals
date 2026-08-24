"""Capture official Wiley issue rosters for provisional history archives."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.wiley import _get, _parse_issue_inventory, _session
from scripts.import_official_roster_evidence import apply_evidence


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _url(config: dict[str, Any], record: dict[str, Any]) -> str:
    return (
        f"https://onlinelibrary.wiley.com/toc/{config['issn']}/"
        f"{record['year']}/{record['volume']}/{record['issue']}"
    )


def _capture(record: dict[str, Any], config: dict[str, Any], archive: Path) -> dict[str, Any]:
    official_url = _url(config, record)
    response = _get(_session(), official_url)
    volume, issue, publication_date, inventory = _parse_issue_inventory(
        response.content, official_url
    )
    if str(volume) != str(record["volume"]) or str(issue) != str(record["issue"]):
        raise ValueError(f"official page identifies {volume}/{issue}, expected {record['volume']}/{record['issue']}")
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in inventory:
        payload = {
            "sequence": len(items) + 1,
            "doi": item.doi,
            "title_en": item.title,
            "source_url": item.source_url,
        }
        if item.is_research_article:
            items.append(payload)
        else:
            excluded.append({
                "doi": item.doi,
                "title_en": item.title,
                "reason": item.exclusion_reason,
            })
    if not items:
        raise ValueError("official Wiley page has no research articles")
    evidence = {
        "schema_version": "1.0",
        "capture_mode": "official-roster-evidence",
        "method": "official-page-read",
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "finalized": True,
        "journal_id": str(record["journal"]).casefold(),
        "issue_id": str(record["issue_id"]),
        "official_url": official_url,
        "publication_date": publication_date,
        "excluded_item_count": len(excluded),
        "excluded_items": excluded,
        "items": items,
    }
    archive_payload = json.loads(archive.read_text(encoding="utf-8"))
    apply_evidence(archive_payload, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap-manifest", type=Path, required=True)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--diagnostics-root", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    manifest = json.loads(args.gap_manifest.read_text(encoding="utf-8"))
    configs = yaml.safe_load((ROOT / "config" / "journals.yml").read_text(encoding="utf-8"))["journals"]
    records = []
    for record in manifest.get("records", []):
        config = configs.get(str(record.get("journal", "")))
        if record.get("category") != "source_pending" or not config:
            continue
        if str(config.get("publisher", "")).casefold() != "wiley":
            continue
        archive = args.api_root / "journals" / str(record["journal"]).casefold() / "issues" / f"{record['issue_id']}.json"
        if archive.exists():
            records.append((record, config, archive))
    captured: list[str] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(_capture, record, config, archive): record for record, config, archive in records}
        for future in as_completed(futures):
            record = futures[future]
            try:
                evidence = future.result()
                _write_json(args.output_root / f"{record['issue_id']}.json", evidence)
                captured.append(str(record["issue_id"]))
            except Exception as exc:  # noqa: BLE001 - preserve per-issue diagnostics
                failure = {"issue_id": str(record["issue_id"]), "error": str(exc)}
                failures.append(failure)
                if args.diagnostics_root:
                    _write_json(args.diagnostics_root / f"{record['issue_id']}.json", failure)
    print(json.dumps({"selected": len(records), "captured": sorted(captured), "failed": sorted(failures, key=lambda x: x["issue_id"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
