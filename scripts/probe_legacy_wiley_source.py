"""Read-only, single-issue feasibility probe for legacy official Wiley TOC transport.

Never writes data, source observations, or READY status. This is not an
authorization to certify the entire publisher DOI/type/order roster.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.wiley import (
    WileyCollectorError,
    _get,
    _parse_issue_inventory,
    _session,
)

ISSUE_ID = "ajae-105-1"
OFFICIAL_URL = "https://onlinelibrary.wiley.com/toc/14678276/2023/105/1"


def probe_once(*, fetch: Any = _get, parse: Any = _parse_issue_inventory) -> dict[str, Any]:
    """Probe exactly one hard-coded first-party issue with one HTTP attempt."""
    response = fetch(_session(), OFFICIAL_URL, attempts=1, timeout=(5, 20))
    volume, issue, _publication_date, items = parse(response.content, OFFICIAL_URL)
    if str(volume) != "105" or str(issue) != "1":
        raise ValueError(f"identity_mismatch: {volume}/{issue}")
    research = [item for item in items if item.is_research_article]
    if not research:
        raise ValueError("no_research_articles")
    dois = [str(item.doi).strip().lower() for item in research]
    ordinals = [item.source_sequence for item in research]
    if any(not doi for doi in dois) or len(set(dois)) != len(dois):
        raise ValueError("missing_or_duplicate_research_doi")
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("duplicate_publisher_ordinal")
    return {
        "issue_id": ISSUE_ID,
        "method": "single-public-official-page-read",
        "result": "transport_parse_succeeded",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "official_url": OFFICIAL_URL,
        "research_count": len(research),
        "excluded_count": len(items) - len(research),
        "no_archive_or_state_mutation": True,
        "full_publisher_completeness_not_yet_accepted": True,
    }


def main() -> int:
    try:
        report = probe_once()
    except (WileyCollectorError, ValueError) as error:
        report = {
            "issue_id": ISSUE_ID,
            "official_url": OFFICIAL_URL,
            "result": "verification_blocked",
            "reason": getattr(error, "code", type(error).__name__),
            "no_archive_or_state_mutation": True,
        }
        print(json.dumps(report, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
