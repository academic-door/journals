"""JEH 86(3) first-party Cambridge issue roster authority (8 research + 4 exclusions)."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.import_official_roster_evidence import apply_evidence, validate_evidence
from scripts.update_journals import ROOT, normalize_issue_content

OFFICIAL_URL = (
    "https://www.cambridge.org/core/journals/journal-of-economic-history/"
    "issue/5C841620FE586A5FCE7C9ADE68540AA9"
)
ISSUE = "jeh-86-3"
RESEARCH_SUFFIXES = ("1521", "1454", "1533", "1478", "151x", "148x", "1491", "1508")
EXCLUDED_SUFFIXES = ("1636", "1624")
COVER_SUFFIXES = ("1162", "1174")


def apply_jeh_current_roster(
    issue: dict[str, Any],
    config: dict[str, Any],
    *,
    root: Path = ROOT,
    detected_dois: list[str] | None = None,
) -> dict[str, Any]:
    if config.get("id") != "jeh" or issue.get("journal_id") != "jeh":
        raise ValueError("JEH Cambridge roster journal identity mismatch")
    relative = str(config.get("official_roster_evidence_path", ""))
    root = root.resolve()
    path = (root / relative).resolve()
    allowed = (root / "data/provenance/official-rosters/jeh").resolve()
    if not relative or not path.is_relative_to(allowed):
        raise ValueError("JEH official evidence must be pinned to the owner JEH provenance subtree")
    ev = json.loads(path.read_text(encoding="utf-8"))
    validate_evidence(ev)
    if ev.get("journal_id") != "jeh" or ev.get("issue_id") != ISSUE:
        raise ValueError("JEH official journal/issue mismatch")
    if ev.get("official_url") != OFFICIAL_URL or ev.get("method") != "official-page-read":
        raise ValueError("JEH first-party Cambridge issue URL/method required")
    stamp = datetime.fromisoformat(ev["captured_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
        raise ValueError("JEH publisher capture must be a real past observation")
    if issue.get("issue_id") != ISSUE:
        return issue
    if str(issue.get("volume")) != "86" or str(issue.get("issue")) != "3":
        raise ValueError("JEH target volume/issue does not match authoritative source")

    rows = ev["items"]
    ex = ev.get("excluded_items", [])
    expected_research = [f"10.1017/s002205072610{x}" for x in RESEARCH_SUFFIXES]
    expected_other = [f"10.1017/s002205072610{x}" for x in EXCLUDED_SUFFIXES]
    expected_other += [f"10.1017/s002205072510{x}" for x in COVER_SUFFIXES]
    if (
        len(rows) != 8 or len(ex) != 4
        or [r.get("doi") for r in rows] != expected_research
        or [r.get("doi") for r in ex] != expected_other
        or [r.get("source_sequence") for r in rows] != list(range(1, 9))
        or [r.get("source_sequence") for r in ex] != [9, 10, 11, 12]
        or [r.get("article_type") for r in ex] != ["front-matter"] * 4
        or [r.get("reason") for r in ex] != ["book_review", "book_review", "cover_matter", "cover_matter"]
        or any(r.get("entry_type") != "Article" for r in rows)
    ):
        raise ValueError("JEH official 12-row DOI/order/type inventory mismatch")
    all_rows = rows + ex
    if any(not str(r.get("source_url", "")).startswith(
        "https://www.cambridge.org/core/journals/journal-of-economic-history/article/"
    ) for r in all_rows):
        raise ValueError("JEH row lacks Cambridge official article provenance")
    official = {r["doi"].casefold() for r in all_rows}
    publishable = {r["doi"].casefold() for r in rows}
    if len(official) != 12:
        raise ValueError("JEH observed publisher DOIs must be unique")
    if detected_dois is not None:
        detected = {str(x).casefold() for x in detected_dois}
        if not publishable <= detected <= official:
            raise ValueError("JEH provisional candidate DOI membership disagrees with official Cambridge")
    collected = [str(a.get("doi", "")).casefold() for a in issue.get("articles", [])]
    if len(set(collected)) != len(collected) or any(not x or x not in official for x in collected):
        raise ValueError("JEH collector introduced duplicate or unofficial DOI")

    candidate = copy.deepcopy(issue)
    candidate["articles"] = []
    candidate.setdefault("quality", {})["excluded_items"] = []
    candidate["quality"]["duplicate_count"] = 0
    candidate = apply_evidence(candidate, ev)
    candidate["quality"]["roster_match_scope"] = "official-publisher-issue-full"
    candidate["quality"]["publisher_page_status"] = "first-party-observed"
    candidate = normalize_issue_content(candidate)
    counts = candidate["content_counts"]
    if (
        len(candidate["articles"]) != 8
        or counts["research_articles"] != 8
        or counts["front_matter"] != 4
        or candidate["quality"]["abstract_en_complete"] != 8
        or candidate["source_status"] != "official_verified"
    ):
        raise ValueError("JEH 86(3) cannot be source complete with wrong article taxonomy")
    return candidate
