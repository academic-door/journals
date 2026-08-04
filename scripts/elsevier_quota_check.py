"""Report Elsevier API quota usage captured by the enrichment pipeline.

Every Elsevier enrichment request now records the X-RateLimit-* response
headers it receives in ``sources.abstract_lookup.attempts[].rate_limit``.
This script aggregates those snapshots from the published issue data and
warns when an API is below 10% of its weekly quota. ``--live`` additionally
makes one Article Metadata request and prints the authoritative headers that
Elsevier returns right now (costs exactly one quota unit).

Exit codes: 0 = healthy (or no snapshots recorded yet), 1 = a recorded quota
is nearly exhausted, 2 = configuration error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.metadata_fallback import (
    ELSEVIER_ARTICLE_METADATA_API,
    QUOTA_WARNING_FRACTION,
    _rate_limit_snapshot,
)

PUBLIC_API = ROOT / "public" / "api" / "v1"


def issue_paths() -> list[Path]:
    journals_dir = PUBLIC_API / "journals"
    if not journals_dir.is_dir():
        return []
    paths: list[Path] = []
    for journal_dir in sorted(journals_dir.iterdir()):
        issues_dir = journal_dir / "issues"
        for name in ("current.json", "detected.json"):
            path = issues_dir / name
            if path.is_file():
                paths.append(path)
    return paths


def collect_snapshots() -> dict[str, dict[str, Any]]:
    """Return the latest quota snapshot per Elsevier API endpoint."""

    per_api: dict[str, dict[str, Any]] = {}
    for path in issue_paths():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        journal_id = str(payload.get("journal_id", path.parent.parent.name))
        for article in payload.get("articles", []):
            lookup = article.get("sources", {}).get("abstract_lookup")
            if not isinstance(lookup, dict):
                continue
            for attempt in lookup.get("attempts", []):
                snapshot = attempt.get("rate_limit")
                if not isinstance(snapshot, dict):
                    continue
                api = str(attempt.get("source", "unknown"))
                record = per_api.setdefault(
                    api,
                    {
                        "limit": None,
                        "remaining": None,
                        "resets_at": "",
                        "journals": set(),
                    },
                )
                if snapshot.get("limit") is not None:
                    record["limit"] = snapshot["limit"]
                if snapshot.get("remaining") is not None:
                    record["remaining"] = snapshot["remaining"]
                if snapshot.get("resets_at"):
                    record["resets_at"] = snapshot["resets_at"]
                record["journals"].add(journal_id)
    return per_api


def report(per_api: dict[str, dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not per_api:
        print("No Elsevier quota snapshots recorded yet; nothing to check.")
        return warnings
    print(f"{'API':<36}{'Remaining':>12}{'Limit':>12}  Resets at")
    for api in sorted(per_api):
        record = per_api[api]
        remaining = record["remaining"]
        limit = record["limit"]
        remaining_text = str(remaining) if remaining is not None else "-"
        limit_text = str(limit) if limit is not None else "-"
        print(
            f"{api:<36}{remaining_text:>12}{limit_text:>12}  "
            f"{record['resets_at'] or '-'}"
        )
        if (
            isinstance(remaining, int)
            and isinstance(limit, int)
            and limit > 0
            and remaining <= max(1, round(limit * QUOTA_WARNING_FRACTION))
        ):
            warnings.append(
                f"{api}: quota nearly exhausted ({remaining}/{limit} remaining) "
                f"seen by {', '.join(sorted(record['journals']))}"
            )
    return warnings


def first_elsevier_doi() -> str:
    for path in issue_paths():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(payload.get("journal_id", "")) not in {
            "jpube",
            "jeem",
            "jde",
            "jue",
            "eer",
            "geb",
            "jet",
            "jebo",
            "jie",
            "jfe",
            "wd",
            "jme",
            "joe",
            "foodpolicy",
            "lup",
        }:
            continue
        for article in payload.get("articles", []):
            doi = str(article.get("doi", "")).strip()
            if doi:
                return doi
    return ""


def live_check(timeout: int = 30) -> dict[str, Any]:
    api_key = os.getenv("ELSEVIER_API_KEY", "").strip()
    if not api_key:
        print("ELSEVIER_API_KEY is not set; cannot run --live.")
        sys.exit(2)
    headers = {"Accept": "application/xml", "X-ELS-APIKey": api_key}
    inst_token = os.getenv("ELSEVIER_INST_TOKEN", "").strip()
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    doi = first_elsevier_doi()
    if not doi:
        print("No published Elsevier DOI found to probe with; cannot run --live.")
        sys.exit(2)
    response = requests.get(
        ELSEVIER_ARTICLE_METADATA_API,
        params={
            "query": f'DOI("{doi}")',
            "view": "COMPLETE",
            "httpAccept": "application/xml",
        },
        headers=headers,
        timeout=timeout,
    )
    snapshot = _rate_limit_snapshot(response) or {}
    snapshot["http_status"] = int(getattr(response, "status_code", 0))
    print(
        f"Live Article Metadata probe (doi={doi}): "
        f"{json.dumps(snapshot, sort_keys=True)}"
    )
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="make one real Article Metadata request and print quota headers",
    )
    parser.add_argument(
        "--write-json",
        default="",
        help="write the aggregated quota report to this JSON path",
    )
    args = parser.parse_args()

    per_api = collect_snapshots()
    if args.write_json:
        payload = {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "apis": {
                api: {
                    "limit": record.get("limit"),
                    "remaining": record.get("remaining"),
                    "resets_at": record.get("resets_at", ""),
                    "journals": sorted(record.get("journals", set())),
                }
                for api, record in sorted(per_api.items())
            },
        }
        target = Path(args.write_json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    warnings = report(per_api)
    if args.live:
        snapshot = live_check()
        remaining = snapshot.get("remaining")
        limit = snapshot.get("limit")
        if (
            isinstance(remaining, int)
            and isinstance(limit, int)
            and limit > 0
            and remaining <= max(1, round(limit * QUOTA_WARNING_FRACTION))
        ):
            warnings.append(
                f"live Article Metadata probe: quota nearly exhausted "
                f"({remaining}/{limit} remaining)"
            )
    for warning in warnings:
        print(f"WARN {warning}")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
