"""Source-bounded first-party American Finance Association current issue JF 81(5).

Crossref detection is a candidate, never roster/order/type authority. Immutable
AFA issue page contains 18 DOI rows: 14 research and 4 non-research.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.import_official_roster_evidence import apply_evidence, validate_evidence
from scripts.update_journals import ROOT, normalize_issue_content

ISSUE = "jf-81-5"
OFFICIAL_URL = "https://afajof.org/issue/volume-81-issue-5/"
EXPECTED_RESEARCH = (
    "70076", "70072", "70065", "70064", "70062", "70040", "70067",
    "70046", "70074", "70058", "70066", "70071", "70068", "70069",
)
EXPECTED_EXCLUDED = ("13353", "70063", "70019", "13354")
EXPECTED_SOURCE_POSITIONS = tuple(range(3, 17))


def apply_jf_current_roster(
    issue: dict[str, Any],
    config: dict[str, Any],
    *,
    root: Path = ROOT,
    detected_dois: list[str] | None = None,
) -> dict[str, Any]:
    if config.get("id") != "jf" or issue.get("journal_id") != "jf":
        raise ValueError("AFA JF evidence cannot be applied to another journal")
    relative = str(config.get("official_roster_evidence_path", ""))
    root = root.resolve()
    path = (root / relative).resolve()
    allowed = (root / "data/provenance/official-rosters/jf").resolve()
    if not relative or not path.is_relative_to(allowed):
        raise ValueError("JF evidence must remain in the exact official provenance subtree")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    validate_evidence(evidence)
    if evidence.get("journal_id") != "jf" or evidence.get("issue_id") != ISSUE:
        raise ValueError("JF source journal/issue identity mismatch")
    if evidence.get("official_url") != OFFICIAL_URL or evidence.get("method") != "official-page-read":
        raise ValueError("JF official AFA issue page and method required")
    captured = datetime.fromisoformat(evidence["captured_at"].replace("Z", "+00:00"))
    if captured.tzinfo is None or captured > datetime.now(timezone.utc):
        raise ValueError("JF publisher observation timestamp invalid")

    if issue.get("issue_id") != ISSUE:
        return issue
    if str(issue.get("volume")) != "81" or str(issue.get("issue")) != "5":
        raise ValueError("JF candidate volume/issue contradiction")

    items = evidence["items"]
    excluded = evidence.get("excluded_items", [])
    if (
        len(items) != 14 or len(excluded) != 4
        or [r.get("doi") for r in items] != [f"10.1111/jofi.{x}" for x in EXPECTED_RESEARCH]
        or [r.get("doi") for r in excluded] != [f"10.1111/jofi.{x}" for x in EXPECTED_EXCLUDED]
        or [r.get("source_sequence") for r in items] != list(EXPECTED_SOURCE_POSITIONS)
        or [r.get("source_sequence") for r in excluded] != [1, 2, 17, 18]
        or [r.get("article_type") for r in excluded] != ["front-matter"] * 4
        or any(r.get("entry_type") != "Article" for r in items)
    ):
        raise ValueError("JF first-party 18 row DOI/type/order inventory changed")
    all_rows = items + excluded
    if any(r.get("source_url") != OFFICIAL_URL for r in all_rows):
        raise ValueError("JF source provenance is not AFA official issue page")
    official = {r["doi"].casefold() for r in all_rows}
    publishable = {r["doi"].casefold() for r in items}
    if len(official) != 18:
        raise ValueError("JF official DOI inventory contains duplicates")
    if detected_dois is not None:
        detected = {str(x).casefold() for x in detected_dois}
        if not publishable <= detected <= official:
            raise ValueError("JF provisional candidate not reconciled to full AFA DOI subset")
    collector = [str(r.get("doi", "")).casefold() for r in issue.get("articles", [])]
    if len(set(collector)) != len(collector) or any(not doi or doi not in official for doi in collector):
        raise ValueError("JF collector supplied duplicate or unobserved publisher DOI")

    # Crossref may misclassify Jay R. Ritter as research. Restore exclusively
    # from actual first-party research rows, while retaining all exclusions.
    candidate = copy.deepcopy(issue)
    candidate["articles"] = []
    candidate.setdefault("quality", {})["excluded_items"] = []
    candidate["quality"]["duplicate_count"] = 0
    candidate = apply_evidence(candidate, evidence)
    candidate["quality"]["roster_match_scope"] = "official-publisher-issue-full"
    candidate["quality"]["publisher_page_status"] = "first-party-observed"
    candidate = normalize_issue_content(candidate)
    counts = candidate["content_counts"]
    if (
        len(candidate["articles"]) != 14
        or counts["research_articles"] != 14
        or counts["front_matter"] != 4
        or counts["publishable_items"] != 14
        or candidate["quality"]["abstract_en_complete"] != 14
        or candidate["source_status"] != "official_verified"
    ):
        raise ValueError("JF official issue cannot pass complete source/typed roster gate")
    return candidate
