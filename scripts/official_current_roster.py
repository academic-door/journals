"""Consume an immutable publisher roster without relaxing publication gates."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.import_official_roster_evidence import (
    _normalized_title,
    apply_evidence,
    validate_evidence,
)
from scripts.update_journals import ROOT, clean_abstract_label, normalize_issue_content


def _pii(value: object) -> str:
    match = re.search(r"(?:/pii/|pii:)([A-Z0-9]+)", str(value or ""), re.I)
    return match.group(1).upper() if match else ""


def apply_current_roster(
    issue: dict[str, Any],
    config: dict[str, Any],
    *,
    root: Path = ROOT,
    detected_dois: list[str] | None = None,
) -> dict[str, Any]:
    relative = str(config.get("official_roster_evidence_path", ""))
    if not relative:
        return issue
    path = (root / relative).resolve()
    allowed = (root / "data/provenance/official-rosters").resolve()
    if not path.is_relative_to(allowed):
        raise ValueError("current roster evidence must stay in official provenance")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    validate_evidence(evidence)
    if evidence["journal_id"] != config["id"]:
        raise ValueError("configured official evidence journal mismatch")
    if evidence["issue_id"] != issue.get("issue_id"):
        return issue
    captured = datetime.fromisoformat(evidence["captured_at"].replace("Z", "+00:00"))
    if captured.tzinfo is None or captured > datetime.now(timezone.utc):
        raise ValueError("official capture timestamp must be an actual past observation")

    items = evidence["items"]
    excluded = evidence.get("excluded_items", [])
    all_rows = items + excluded
    positions = [row.get("source_sequence") for row in all_rows]
    if not all(isinstance(n, int) for n in positions) or sorted(positions) != list(range(1, len(all_rows) + 1)):
        raise ValueError("official source sequence must include every excluded row")
    if [row["source_sequence"] for row in items] != sorted(
        row["source_sequence"] for row in items
    ):
        raise ValueError("publishable sequence contradicts official source order")
    if len({_pii(row.get("source_id")) for row in all_rows}) != len(all_rows) or any(
        not _pii(row.get("source_id")) for row in all_rows
    ):
        raise ValueError("official source PIIs must be nonempty and unique")
    official_dois = {row["doi"].casefold() for row in all_rows}
    if len(official_dois) != len(all_rows):
        raise ValueError("official included/excluded DOI identities overlap")
    if detected_dois is not None:
        detected = set(map(str.casefold, detected_dois))
        publishable = {row["doi"].casefold() for row in items}
        # Metadata discovery may omit an editorial-board DOI. It may never
        # omit a publishable DOI or introduce an unobserved identity.
        if not publishable <= detected <= official_dois:
            raise ValueError("detected DOI set changed since the official observation")

    by_doi = {row["doi"].casefold(): row for row in items}
    by_pii = {_pii(row["source_id"]): row for row in items}
    candidate = copy.deepcopy(issue)
    for article in candidate.get("articles", []):
        if not str(article.get("doi", "")).strip():
            row = by_pii.get(_pii(article.get("source_url")))
            if row is None or _normalized_title(article.get("title_en")) != _normalized_title(row["title_en"]):
                raise ValueError("missing collector DOI lacks matching official PII/title")
            article["doi"] = row["doi"]
            article["paper_id"] = f"doi:{row['doi']}"
            article.setdefault("sources", {})["doi"] = row.get("doi_source_url", evidence["official_url"])
    collector_dois = [str(row.get("doi", "")).casefold() for row in candidate.get("articles", [])]
    if len(set(collector_dois)) != len(collector_dois):
        raise ValueError("collector DOI roster contains duplicates")
    for article in candidate.get("articles", []):
        doi = str(article.get("doi", "")).casefold()
        row = by_doi.get(doi)
        if row is None:
            raise ValueError("collector contains an unobserved research DOI")
        observed_pii = _pii(article.get("source_url"))
        if observed_pii and observed_pii != _pii(row["source_id"]):
            raise ValueError("collector DOI/PII binding contradicts official evidence")
        if _normalized_title(article.get("title_en")) != _normalized_title(row["title_en"]):
            raise ValueError("collector title contradicts official evidence")
        for field in ("authors", "abstract_en"):
            missing = not article.get(field)
            if field == "abstract_en":
                missing = not clean_abstract_label(article.get(field))
            if missing:
                if not row.get(field):
                    raise ValueError(f"official evidence cannot restore missing {field}")
                article[field] = copy.deepcopy(row[field])
                article.setdefault("sources", {})[field] = row.get(
                    "abstract_source_url" if field == "abstract_en" else "authors_source_url",
                    row["source_url"],
                )
    excluded_by_doi = {row["doi"].casefold(): row for row in excluded}
    for row in candidate.get("quality", {}).get("excluded_items", []):
        doi = str(row.get("doi", "")).casefold()
        pii = _pii(row.get("source_url") or row.get("source_id"))
        matched = excluded_by_doi.get(doi) or next(
            (x for x in excluded if pii and _pii(x["source_id"]) == pii), None
        )
        if matched is None or _normalized_title(row.get("title_en")) != _normalized_title(matched["title_en"]):
            raise ValueError("collector exclusion contradicts official evidence")
    candidate = apply_evidence(candidate, evidence)
    candidate["quality"]["excluded_items"] = copy.deepcopy(excluded)
    candidate["quality"]["flags"] = [
        flag for flag in candidate["quality"]["flags"]
        if flag != "publisher_rss_reverse_order_normalized"
    ]
    if evidence.get("publisher_issue_status") == "in-progress":
        candidate["quality"]["official_roster_evidence"]["publisher_issue_status"] = "in-progress"
        candidate["publisher_issue_status"] = "in-progress"
    return normalize_issue_content(candidate)
