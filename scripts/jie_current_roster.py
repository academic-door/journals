"""JIE 163 authoritative current ScienceDirect source DOI/order/two editorial roster.

All eight accepted translations are preserved. Unlike research completeness,
official publish order and editorial exclusion completeness were not yet true.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.import_official_roster_evidence import apply_evidence, validate_evidence
from scripts.update_journals import ROOT, normalize_issue_content

ISSUE = "jie-163-c"
OFFICIAL = "https://www.sciencedirect.com/journal/journal-of-international-economics/vol/163/suppl/C"
PIIS = (
    "S0022199626000942", "S0022199626000851", "S0022199625001606",
    "S0022199625001643", "S0022199625001618", "S0022199626000097",
    "S0022199626000152", "S0022199626000218", "S0022199626000206",
    "S0022199626000802",
)
RESEARCH = (
    "10.1016/j.jinteco.2025.104203", "10.1016/j.jinteco.2025.104207",
    "10.1016/j.jinteco.2025.104204", "10.1016/j.jinteco.2026.104219",
    "10.1016/j.jinteco.2026.104225", "10.1016/j.jinteco.2026.104231",
    "10.1016/j.jinteco.2026.104230", "10.1016/j.jinteco.2026.104290",
)
EXCLUDED = ("10.1016/s0022-1996(26)00094-2", "10.1016/j.jinteco.2026.104295")


def apply_jie_current_roster(
    issue: dict[str, Any],
    config: dict[str, Any],
    *,
    root: Path = ROOT,
    detected_dois: list[str] | None = None,
) -> dict[str, Any]:
    if config.get("id") != "jie" or issue.get("journal_id") != "jie":
        raise ValueError("JIE official roster cannot be applied to another journal")
    relative = str(config.get("official_roster_evidence_path", ""))
    root = root.resolve()
    path = (root / relative).resolve()
    allowed = (root / "data/provenance/official-rosters/jie").resolve()
    if not relative or not path.is_relative_to(allowed):
        raise ValueError("JIE evidence must be scoped to owner publisher provenance subtree")
    ev = json.loads(path.read_text(encoding="utf-8"))
    validate_evidence(ev)
    if ev.get("journal_id") != "jie" or ev.get("issue_id") != ISSUE:
        raise ValueError("JIE first-party source identity mismatch")
    if ev.get("official_url") != OFFICIAL or ev.get("method") != "official-page-read":
        raise ValueError("JIE original publisher issue URL is required")
    seen = datetime.fromisoformat(ev["captured_at"].replace("Z", "+00:00"))
    if seen.tzinfo is None or seen > datetime.now(timezone.utc):
        raise ValueError("JIE original observation time missing/future")

    if issue.get("issue_id") != ISSUE:
        return issue
    if str(issue.get("volume")) != "163":
        raise ValueError("JIE source target volume contradiction")

    items, excluded = ev["items"], ev.get("excluded_items", [])
    if (
        len(items) != 8 or len(excluded) != 2
        or [x.get("doi") for x in items] != list(RESEARCH)
        or [x.get("source_sequence") for x in items] != list(range(3, 11))
        or [x.get("doi") for x in excluded] != list(EXCLUDED)
        or [x.get("source_sequence") for x in excluded] != [1, 2]
        or [x.get("article_type") for x in excluded] != ["editorial", "editorial"]
        or [x.get("reason") for x in excluded] != ["editorial_material"] * 2
        or any(x.get("publisher_article_type") != "research article" for x in items)
    ):
        raise ValueError("JIE first-party exact 8 research +2 editorial DOI/type/order mismatch")
    all_rows = items + excluded
    if [str(x.get("source_id", "")).split(":", 1)[-1].upper()
            for x in sorted(all_rows, key=lambda x: x["source_sequence"])] != list(PIIS):
        raise ValueError("JIE PII sequence does not match official publisher")
    seen_dois = {x["doi"].casefold() for x in all_rows}
    if len(seen_dois) != 10:
        raise ValueError("JIE publisher DOI entries are duplicated")
    if detected_dois is not None:
        detected = {str(x).casefold() for x in detected_dois}
        if not detected <= seen_dois:
            raise ValueError("JIE candidate contains DOI not in official publisher issue")
    archive = list(issue.get("articles", []))
    if {str(x.get("doi", "")).casefold() for x in archive} != set(RESEARCH) or len(archive) != 8:
        raise ValueError("JIE current archive must be the same publisher eight DOI research set")
    by_doi = {x["doi"].casefold(): x for x in items}
    for a in archive:
        doi = a["doi"].casefold()
        m = re.search(r"/pii/(S[A-Z0-9]+)", str(a.get("source_url", "")), re.I)
        if m and m.group(1).upper() != by_doi[doi]["source_id"].split(":", 1)[1]:
            raise ValueError("JIE archived DOI/PII binding conflicts with publisher")

    candidate = copy.deepcopy(issue)
    candidate.setdefault("quality", {})["excluded_items"] = []
    candidate["quality"]["duplicate_count"] = 0
    candidate = apply_evidence(candidate, ev)
    candidate["quality"]["roster_match_scope"] = "official-publisher-issue-full"
    candidate["quality"]["publisher_page_status"] = "first-party-observed"
    candidate["quality"].pop("browser_order_verification", None)
    candidate["quality"].pop("browser_capture", None)
    candidate = normalize_issue_content(candidate)
    counts = candidate["content_counts"]
    if (
        len(candidate["articles"]) != 8
        or counts["research_articles"] != 8
        or counts["editorial_material"] != 2
        or candidate["quality"]["official_item_count"] != 10
        or candidate["quality"]["translation_complete"] != 8
        or candidate["source_status"] != "official_verified"
    ):
        raise ValueError("JIE 163 cannot pass first-party 8+2 acceptance")
    return candidate
