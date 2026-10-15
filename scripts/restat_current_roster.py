"""Strict MIT Press RESTAT 108(5) current-issue authority from immutable first-party evidence.

This deliberately does not relax the shared Crossref provisional-roster gate or
Elsevier's PII-based current-issue adapter. The official 24-row source inventory
is the only authority for publication order, type and DOI membership.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.import_official_roster_evidence import apply_evidence, validate_evidence
from scripts.update_journals import ROOT, normalize_issue_content

OFFICIAL_ISSUE = "restat-108-5"
OFFICIAL_URL = "https://direct.mit.edu/rest/issue/108/5"
EVIDENCE_ROOT = "data/provenance/official-rosters/"


def apply_restat_current_roster(
    issue: dict[str, Any],
    config: dict[str, Any],
    *,
    root: Path = ROOT,
    detected_dois: list[str] | None = None,
) -> dict[str, Any]:
    """Restore only RESTAT 108(5) using its exact independently observed TOC.

    The snapshot is a bounded publisher observation, not a permanently fresh
    monitor; future/other issues remain subject to the original source gate.
    No current public payload or production data branch is mutated here.
    """

    if config.get("id") != "restat" or issue.get("journal_id") != "restat":
        raise ValueError("MIT RESTAT evidence cannot be applied to another journal")
    relative = str(config.get("official_roster_evidence_path", ""))
    root = root.resolve()
    path = (root / relative).resolve()
    trusted_root = (root / EVIDENCE_ROOT).resolve()
    if not relative or not path.is_relative_to(trusted_root):
        raise ValueError("RESTAT official evidence must reside in the owner provenance root")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    validate_evidence(evidence)
    if evidence.get("journal_id") != "restat" or evidence.get("issue_id") != OFFICIAL_ISSUE:
        raise ValueError("RESTAT evidence identity mismatch")
    if evidence.get("official_url") != OFFICIAL_URL:
        raise ValueError("RESTAT source must be the exact MIT Press official issue page")
    if evidence.get("method") != "official-page-read":
        raise ValueError("RESTAT source needs publisher article-page verification")
    captured = datetime.fromisoformat(evidence["captured_at"].replace("Z", "+00:00"))
    if captured.tzinfo is None or captured > datetime.now(timezone.utc):
        raise ValueError("RESTAT evidence capture timestamp is missing/future")

    # The pinned evidence must not authorize an unrelated past or future issue.
    if issue.get("issue_id") != OFFICIAL_ISSUE:
        return issue
    if str(issue.get("volume")) != "108" or str(issue.get("issue")) != "5":
        raise ValueError("RESTAT volume/issue contradiction")

    items = evidence["items"]
    excluded = evidence.get("excluded_items", [])
    if len(items) != 23 or len(excluded) != 1:
        raise ValueError("RESTAT 108(5) first-party inventory is not 23+1")
    if [str(x.get("entry_type")) for x in items] != ["Article"] * 21 + ["Comment", "Response"]:
        raise ValueError("RESTAT publisher section taxonomy differs from the observed TOC")
    if any(x.get("source_sequence") != n for n, x in enumerate(items, 1)):
        raise ValueError("RESTAT publisher research/comment order changed")
    correction = excluded[0]
    if (
        correction.get("doi", "").casefold() != "10.1162/rest.x.1827"
        or correction.get("source_sequence") != 24
        or correction.get("article_type") != "correction"
        or correction.get("reason") != "correction_notice"
    ):
        raise ValueError("RESTAT publisher correction/exclusion is not verified")
    for row in items + excluded:
        if not str(row.get("source_url", "")).startswith("https://direct.mit.edu/rest/article/108/5/"):
            raise ValueError("RESTAT article source is not an issue-specific publisher page")

    publisher_dois = [x["doi"].casefold() for x in items + excluded]
    official_dois = set(publisher_dois)
    if len(official_dois) != 24:
        raise ValueError("RESTAT publisher DOI roster is not unique")
    if detected_dois is not None and set(map(str.casefold, detected_dois)) != official_dois:
        raise ValueError("RESTAT detected DOI identities contradict publisher evidence")

    collected = [str(a.get("doi", "")).strip().casefold() for a in issue.get("articles", [])]
    if len(collected) != len(set(collected)) or any(not x or x not in official_dois for x in collected):
        raise ValueError("RESTAT provisional collector has duplicate or non-publisher DOI")
    # Crossref is not permitted to determine publisher order, type, exclusions,
    # abstracts or authority. Restore from the publisher first-party evidence.
    # Translation cache / known-good content is separately reconciled downstream.
    candidate = copy.deepcopy(issue)
    candidate["articles"] = []
    candidate.setdefault("quality", {})["excluded_items"] = []
    candidate["quality"]["duplicate_count"] = 0
    candidate = apply_evidence(candidate, evidence)
    candidate["quality"]["roster_match_scope"] = "official-publisher-issue-full"
    candidate["quality"]["publisher_page_status"] = "first-party-observed"
    candidate = normalize_issue_content(candidate)
    if (
        len(candidate["articles"]) != 23
        or candidate["content_counts"]["research_articles"] != 21
        or candidate["content_counts"]["comments"] != 2
        or candidate["content_counts"]["corrections"] != 1
        or candidate["source_status"] != "official_verified"
        or candidate["quality"]["abstract_en_complete"] != 23
    ):
        raise ValueError("RESTAT 108(5) cannot pass typed complete-source gate")
    return candidate
