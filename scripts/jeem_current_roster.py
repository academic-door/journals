"""JEEM 140 bounded same-volume source recovery from actual publisher 9+2 roster.

A prior READY 4/4 on the 2026-08-03 snapshot is not current first-party
completeness. Scope to volume 140 only; preserve those existing translations.
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

ISSUE = "jeem-140-c"
OFFICIAL = (
    "https://www.sciencedirect.com/journal/"
    "journal-of-environmental-economics-and-management/vol/140/suppl/C"
)
PIIS = (
    "S0095069626001415", "S0095069626001178", "S009506962600121X",
    "S0095069626001294", "S0095069626001130", "S0095069626001312",
    "S0095069626001191", "S0095069626001208", "S0095069626001324",
    "S0095069626001142", "S0095069626001300",
)
RESEARCH = ("103397", "103401", "103409", "103393", "103411",
            "103399", "103400", "103412", "103410")


def apply_jeem_current_roster(
    issue: dict[str, Any],
    config: dict[str, Any],
    *,
    root: Path = ROOT,
    detected_dois: list[str] | None = None,
) -> dict[str, Any]:
    if config.get("id") != "jeem" or issue.get("journal_id") != "jeem":
        raise ValueError("JEEM publisher observation cannot affect unrelated journal")
    relative = str(config.get("official_roster_evidence_path", ""))
    root = root.resolve()
    path = (root / relative).resolve()
    allowed = (root / "data/provenance/official-rosters/jeem").resolve()
    if not relative or not path.is_relative_to(allowed):
        raise ValueError("JEEM first-party evidence path must remain in owner provenance")
    ev = json.loads(path.read_text(encoding="utf-8"))
    validate_evidence(ev)
    if ev.get("journal_id") != "jeem" or ev.get("issue_id") != ISSUE:
        raise ValueError("JEEM first-party journal and issue identity mismatch")
    if ev.get("official_url") != OFFICIAL or ev.get("method") != "official-page-read":
        raise ValueError("JEEM exact ScienceDirect publisher issue is required")
    captured = datetime.fromisoformat(ev["captured_at"].replace("Z", "+00:00"))
    if captured.tzinfo is None or captured > datetime.now(timezone.utc):
        raise ValueError("JEEM issue capture must have a truthful past timestamp")
    if issue.get("issue_id") != ISSUE:
        return issue
    if str(issue.get("volume")) != "140":
        raise ValueError("JEEM volume 140 identity mismatch")

    items = ev["items"]
    exclusions = ev.get("excluded_items", [])
    research_dois = [f"10.1016/j.jeem.2026.{x}" for x in RESEARCH]
    excluded_dois = [
        "10.1016/s0095-0696(26)00141-5", "10.1016/j.jeem.2026.103394"
    ]
    if (
        len(items) != 9 or len(exclusions) != 2
        or [x.get("doi") for x in items] != research_dois
        or [x.get("doi") for x in exclusions] != excluded_dois
        or [x.get("source_sequence") for x in items] != [2, 3, 4, 5, 6, 7, 8, 9, 11]
        or [x.get("source_sequence") for x in exclusions] != [1, 10]
        or [x.get("article_type") for x in exclusions] != ["editorial", "correction"]
        or [x.get("reason") for x in exclusions] != ["editorial_material", "correction_notice"]
        or any(x.get("publisher_article_type") != "research article" for x in items)
    ):
        raise ValueError("JEEM publisher 11-item DOI/PII/type/order observation contradicts exact source")
    all_rows = items + exclusions
    evidence_piis = [str(x.get("source_id", "")).removeprefix("pii:").upper()
                     for x in sorted(all_rows, key=lambda x: x["source_sequence"])]
    if evidence_piis != list(PIIS):
        raise ValueError("JEEM official PII order or publisher row changed")
    all_dois = {x["doi"].casefold() for x in all_rows}
    if len(all_dois) != 11:
        raise ValueError("JEEM official DOI inventory must be unique")
    if detected_dois is not None and not set(map(str.casefold, detected_dois)) <= all_dois:
        raise ValueError("JEEM Crossref/RSS candidate added an unobserved publisher DOI")
    existing = [str(a.get("doi", "")).casefold() for a in issue.get("articles", [])]
    if len(set(existing)) != len(existing) or any(not doi or doi not in all_dois for doi in existing):
        raise ValueError("JEEM old archive contains duplicate or publisher-absent DOI")
    matching = {x["doi"].casefold(): x for x in items}
    for article in issue.get("articles", []):
        row = matching.get(str(article.get("doi", "")).casefold())
        if row is None:
            raise ValueError("JEEM old archive includes unpublishable DOI")
        match = re.search(r"/pii/(S[A-Z0-9]+)", str(article.get("source_url", "")), re.I)
        if match and match.group(1).upper() != row["source_id"].split(":", 1)[1]:
            raise ValueError("JEEM old DOI/PII binding contradicts ScienceDirect")

    candidate = copy.deepcopy(issue)
    candidate.setdefault("quality", {})["excluded_items"] = []
    candidate["quality"]["duplicate_count"] = 0
    candidate = apply_evidence(candidate, ev)
    candidate["quality"]["roster_match_scope"] = "official-publisher-issue-full"
    candidate["quality"]["publisher_page_status"] = "first-party-observed"
    # Prior August 3 browser cardinality 4/4 was truthful then but MUST NOT
    # masquerade as proof of this new October 18, 11-item observation.
    candidate["quality"].pop("browser_order_verification", None)
    candidate["quality"].pop("browser_capture", None)
    candidate["quality"]["flags"] = [
        f for f in candidate["quality"].get("flags", [])
        if f != "publisher_html_blocked_sciencedirect_rss_fallback"
    ]
    candidate = normalize_issue_content(candidate)
    counts = candidate["content_counts"]
    if (
        len(candidate["articles"]) != 9
        or counts["research_articles"] != 9
        or counts["editorial_material"] != 1
        or counts["corrections"] != 1
        or candidate["quality"]["abstract_en_complete"] != 9
        or candidate["source_status"] != "official_verified"
    ):
        raise ValueError("JEEM 140 source-qualified nine research cannot pass strict gate")
    return candidate
