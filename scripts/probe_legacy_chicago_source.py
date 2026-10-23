"""Read-only, one-request legacy UChicago JPE publisher-TOC feasibility probe.

No generated data or publisher observation is persisted. Do not treat transport
success alone as certification of exhaustive publisher issue coverage.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.chicago import _get, _parse_issue_page, _session

ISSUE_ID = "jpe-131-1"
OFFICIAL_URL = "https://www.journals.uchicago.edu/toc/jpe/2023/131/1"


def probe_once(*, fetch: Any = _get, parse: Any = _parse_issue_page) -> dict[str, Any]:
    response = fetch(_session(), OFFICIAL_URL, attempts=1, timeout=20)
    payload = parse(response.content, OFFICIAL_URL)
    if str(payload.get("volume")) != "131" or str(payload.get("issue")) != "1":
        raise ValueError("publisher_identity_mismatch")
    items = payload.get("items", [])
    if not isinstance(items, list) or not items:
        raise ValueError("publisher_inventory_empty")
    dois = [str(item.get("doi", "")).strip().lower() for item in items]
    ordinals = [item.get("source_sequence") for item in items]
    if any(not doi for doi in dois) or len(set(dois)) != len(dois):
        raise ValueError("missing_or_duplicate_doi")
    if any(not item.get("title_en") for item in items):
        raise ValueError("missing_publisher_title")
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("duplicate_publisher_ordinal")
    return {
        "issue_id": ISSUE_ID,
        "official_url": OFFICIAL_URL,
        "method": "single-public-official-page-read",
        "result": "transport_parse_succeeded",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "toc_item_count": len(items),
        "exclusion_count": sum(bool(item.get("exclusion_reason")) for item in items),
        "flags": payload.get("flags", []),
        "no_archive_or_state_mutation": True,
        "publisher_completeness_not_yet_accepted": True,
    }


def main() -> int:
    try:
        report = probe_once()
    except (requests.RequestException, ValueError) as exc:
        report = {
            "issue_id": ISSUE_ID,
            "official_url": OFFICIAL_URL,
            "result": "verification_blocked",
            "reason": (
                f"http_{exc.response.status_code}"
                if isinstance(exc, requests.HTTPError) and exc.response is not None
                else type(exc).__name__
            ),
            "no_archive_or_state_mutation": True,
        }
        print(json.dumps(report, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
