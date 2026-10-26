"""Bounded first-party Springer current issue collector for B8 JEGROWTH.

Do not use Crossref deposits or a stale alias as proof of an official roster.
A future issue needs a separately observed complete Springer TOC before this
bounded adapter can advance; otherwise the previous READY snapshot is retained.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "tests" / "fixtures" / "b8" / "springer-source-stage.json"


def verified_target(config: dict, stage: dict | None = None) -> dict:
    if str(config.get("id")) != "jegrowth":
        raise ValueError("B8 Springer adapter is approved only for JEGROWTH")
    if str(config.get("issn")) != "1381-4338":
        raise ValueError("JEGROWTH ISSN mismatch")
    url = str(config.get("current_issue_url", ""))
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "link.springer.com":
        raise ValueError("not an official Springer publisher host")
    source = stage if stage is not None else json.loads(STAGE.read_text(encoding="utf-8"))
    rows = [x for x in source.get("journals", []) if x.get("journal") == "JEGROWTH"]
    if len(rows) != 1:
        raise ValueError("unique JEGROWTH source evidence missing")
    row = rows[0]
    if not row.get("verified_full_displayed_toc"):
        raise ValueError("Springer official issue order not verified")
    if row.get("source_authority") != "first_party_springer_full_issue":
        raise ValueError("wrong Springer source authority")
    if row.get("official_url") != url or row.get("issue_id") != "jegrowth-31-3":
        raise ValueError("publisher current URL not equal to approved exact issue")
    if not row.get("source_items") or len(row["source_items"]) != row.get("source_item_count"):
        raise ValueError("missing pinned first-party DOI list")
    return row


def fetch_current_issue(config: dict, *, expected_volume: str = "", expected_issue: str = "") -> dict:
    """Recapture live official pages; return a schema-valid, untranslated issue.

    The shared update routine applies translation cache / translation checks,
    then refuses publication until all quality and freshness gates pass.
    """
    stage = verified_target(config)
    if expected_volume and expected_volume != "31":
        raise ValueError("future Springer issue has no verified exact TOC")
    if expected_issue and expected_issue != "3":
        raise ValueError("future Springer issue has no verified exact TOC")
    from scripts.capture_springer_roster_evidence import capture_record
    from scripts.b8_springer_pilot import make_candidate, verify_capture

    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    evidence = capture_record(
        {
            "journal": "JEGROWTH",
            "issue_id": stage["issue_id"],
            "volume": "31",
            "issue": "3",
            "official_url": stage["official_url"],
            "springer_journal_code": "10887",
            "issn": config["issn"],
        },
        captured_at=captured_at,
        detail_workers=3,
    )
    verify_capture(evidence, stage)
    return make_candidate(
        evidence,
        {"name": config["name"], "issn": config["issn"]},
        stage,
    )
