"""Publisher-qualified University of Wisconsin Press Land Economics 102(4) roster."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.import_official_roster_evidence import apply_evidence, validate_evidence
from scripts.update_journals import ROOT, normalize_issue_content

ISSUE = "landecon-102-4"
OFFICIAL = "https://le.uwpress.org/content/102/4"
SUFFIXES = (
    "011224-0004r2", "052425-0039r1", "042225-0032r1", "040325-0023r1",
    "011525-0001r1", "060225-0044r1", "101524-0096r1", "060225-0044r2",
)


def apply_landecon_current_roster(
    issue: dict[str, Any],
    config: dict[str, Any],
    *,
    root: Path = ROOT,
    detected_dois: list[str] | None = None,
) -> dict[str, Any]:
    if config.get("id") != "landecon" or issue.get("journal_id") != "landecon":
        raise ValueError("UW Press roster cannot apply to another journal")
    relative = str(config.get("official_roster_evidence_path", ""))
    root = root.resolve()
    path = (root / relative).resolve()
    approved = (root / "data/provenance/official-rosters/landecon").resolve()
    if not relative or not path.is_relative_to(approved):
        raise ValueError("UW issue source evidence must be scoped to Land Economics owner provenance")
    ev = json.loads(path.read_text(encoding="utf-8"))
    validate_evidence(ev)
    if ev.get("journal_id") != "landecon" or ev.get("issue_id") != ISSUE:
        raise ValueError("UW publisher evidence journal/issue mismatch")
    if ev.get("official_url") != OFFICIAL or ev.get("method") != "official-page-read":
        raise ValueError("UW Press first-party issue official source required")
    captured = datetime.fromisoformat(ev["captured_at"].replace("Z", "+00:00"))
    if captured.tzinfo is None or captured > datetime.now(timezone.utc):
        raise ValueError("UW Press official capture is missing or in the future")
    if issue.get("issue_id") != ISSUE:
        return issue
    if str(issue.get("volume")) != "102" or str(issue.get("issue")) != "4":
        raise ValueError("Land Economics candidate volume or issue mismatch")

    items = ev["items"]
    excluded = ev.get("excluded_items", [])
    if (
        len(items) != 8 or excluded
        or [x.get("doi") for x in items] != [f"10.3368/le.102.4.{x}" for x in SUFFIXES]
        or [x.get("source_sequence") for x in items] != list(range(1, 9))
        or any(x.get("entry_type") != "Article" for x in items)
        or any(not str(x.get("source_url", "")).startswith("https://le.uwpress.org/content/102/4/") for x in items)
    ):
        raise ValueError("UW Press issue 102(4) official DOI/order/type source changed")
    official = {x["doi"].casefold() for x in items}
    if detected_dois is not None and set(map(str.casefold, detected_dois)) != official:
        raise ValueError("UW Press Crossref candidate DOI identities do not match official issue")
    existing = [str(x.get("doi", "")).casefold() for x in issue.get("articles", [])]
    if len(existing) != len(set(existing)) or any(not x or x not in official for x in existing):
        raise ValueError("Land Economics provisional collector contains unverified/duplicate DOI")
    candidate = copy.deepcopy(issue)
    candidate["articles"] = []
    candidate.setdefault("quality", {})["excluded_items"] = []
    candidate["quality"]["duplicate_count"] = 0
    candidate = apply_evidence(candidate, ev)
    candidate["quality"]["roster_match_scope"] = "official-publisher-issue-full"
    candidate["quality"]["publisher_page_status"] = "first-party-observed"
    candidate = normalize_issue_content(candidate)
    if (
        len(candidate["articles"]) != 8
        or candidate["content_counts"]["research_articles"] != 8
        or candidate["content_counts"]["official_items"] != 8
        or candidate["quality"]["abstract_en_complete"] != 8
        or candidate["source_status"] != "official_verified"
    ):
        raise ValueError("Land Economics 102(4) failed complete publisher source gate")
    return candidate
