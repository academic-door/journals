"""Build a 2026 authoritative discovery shard from rendered ScienceDirect evidence.

The input is a durable normalized observation of explicit publisher-listed entries.
Year-range headers are never expanded. Crossref is not consulted here.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

SCHEMA_VERSION = "1.1"
AUTHORITY = "official_archive_snapshot"
COLLECTOR_REVISION = "elsevier-browser-authority-v1"
ALLOWED_AUDIT_STATUSES = {"expected", "not_yet_published"}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def _load_journals(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    journals = payload.get("journals", {}) if isinstance(payload, dict) else {}
    if not isinstance(journals, dict):
        raise ValueError(f"{path}: journals must be a mapping")
    return journals


def _timezone_aware(value: str) -> None:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")


def _validate_sciencedirect_url(url: str, *, source_path_prefix: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() != "www.sciencedirect.com":
        raise ValueError(f"not an official ScienceDirect HTTPS URL: {url}")
    if not parsed.path.startswith(source_path_prefix):
        raise ValueError(f"entry escaped configured journal surface: {url}")


def build_state(evidence: dict[str, Any], journals_config: dict[str, Any]) -> dict[str, Any]:
    if evidence.get("publisher") != "Elsevier":
        raise ValueError("publisher must be Elsevier")
    if evidence.get("authority") != "official_publisher_archive":
        raise ValueError("evidence authority must be official_publisher_archive")
    contract = evidence.get("selection_contract") or {}
    if not contract.get("explicit_entries_only"):
        raise ValueError("explicit_entries_only contract is required")
    if not contract.get("range_headers_are_context_only"):
        raise ValueError("range headers must remain context-only")

    observed_at = str(evidence.get("observed_at", "")).strip()
    if not observed_at:
        raise ValueError("observed_at is required")
    _timezone_aware(observed_at)
    audit_window_end = str(evidence.get("audit_window_end", "")).strip()
    if not audit_window_end:
        raise ValueError("audit_window_end is required")

    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": observed_at,
        "expected_issue_exclusions": {},
        "issues": {},
        "discovery": {},
    }

    journals = evidence.get("journals")
    if not isinstance(journals, dict) or not journals:
        raise ValueError("journals evidence must be a non-empty mapping")

    for journal_key in sorted(journals):
        observed = journals[journal_key]
        definition = journals_config.get(journal_key)
        if not isinstance(definition, dict):
            raise ValueError(f"unknown configured journal: {journal_key}")
        if str(definition.get("publisher", "")) != "Elsevier":
            raise ValueError(f"{journal_key}: configured publisher is not Elsevier")

        journal_id = str(observed.get("journal_id", "")).strip()
        if journal_id != str(definition.get("id", "")).strip():
            raise ValueError(f"{journal_key}: journal_id mismatch")

        source_url = str(observed.get("source_url", "")).strip()
        configured_source = str(definition.get("current_issue_url", "")).strip().rstrip("/")
        if source_url.rstrip("/") != configured_source:
            raise ValueError(f"{journal_key}: source_url does not match configured ScienceDirect surface")
        parsed_source = urlparse(source_url)
        if parsed_source.scheme != "https" or (parsed_source.hostname or "").casefold() != "www.sciencedirect.com":
            raise ValueError(f"{journal_key}: source_url is not official ScienceDirect")
        source_prefix = parsed_source.path.rsplit("/issues", 1)[0] + "/vol/"

        page_title = str(observed.get("page_title", ""))
        if "All Journal Issues" not in page_title or "ScienceDirect.com by Elsevier" not in page_title:
            raise ValueError(f"{journal_key}: page title is not an Elsevier All Journal Issues surface")
        if not str(observed.get("capture_reference", "")).strip():
            raise ValueError(f"{journal_key}: capture_reference is required")
        if not str(observed.get("transport", "")).strip():
            raise ValueError(f"{journal_key}: transport is required")

        entries = observed.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{journal_key}: entries must be non-empty")

        issue_ids: list[str] = []
        issue_years: dict[str, int] = {}
        issue_refs: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()

        for raw in entries:
            if not isinstance(raw, dict):
                raise ValueError(f"{journal_key}: entry must be an object")
            issue_id = str(raw.get("issue_id", "")).strip()
            volume = str(raw.get("volume", "")).strip()
            issue = str(raw.get("issue", "")).strip().casefold()
            year = int(raw.get("year", 0))
            expected_id = f"{journal_id}-{volume}-{issue}"
            if issue_id != expected_id:
                raise ValueError(f"{journal_key}: issue_id mismatch for {issue_id}")
            if issue_id in seen:
                raise ValueError(f"{journal_key}: duplicate issue_id {issue_id}")
            seen.add(issue_id)
            if year != 2026:
                raise ValueError(f"{journal_key}: only 2026 entries are allowed in this shard")
            label = str(raw.get("label", "")).strip()
            if not label or volume not in label:
                raise ValueError(f"{journal_key}: explicit entry label missing volume for {issue_id}")
            official_url = str(raw.get("official_url", "")).strip()
            _validate_sciencedirect_url(official_url, source_path_prefix=source_prefix)

            audit_status = str(raw.get("audit_status", "")).strip()
            if audit_status not in ALLOWED_AUDIT_STATUSES:
                raise ValueError(f"{journal_key}: invalid audit_status for {issue_id}")

            issue_ids.append(issue_id)
            issue_years[issue_id] = year
            issue_refs[issue_id] = {
                "journal": journal_key,
                "year": year,
                "volume": volume,
                "issue": issue,
                "official_url": official_url,
                "label": label,
                "publication_label": str(raw.get("publication_label", "")).strip(),
                "evidence_source_url": source_url,
                "evidence_transport": str(observed.get("transport", "")).strip(),
                "capture_reference": str(observed.get("capture_reference", "")).strip(),
            }

            if audit_status == "not_yet_published":
                publication_label = str(raw.get("publication_label", "")).strip()
                if not publication_label:
                    raise ValueError(f"{journal_key}: future entry lacks publication_label for {issue_id}")
                state["expected_issue_exclusions"][issue_id] = {
                    "status": "not_yet_published",
                    "journal": journal_key,
                    "year": year,
                    "volume": volume,
                    "issue": issue,
                    "official_url": official_url,
                    "reason": (
                        f"Publisher-listed future/preregistered entry at observation time: "
                        f"{publication_label}."
                    ),
                    "rediscover": True,
                    "recorded_at": audit_window_end,
                }

        state["discovery"][journal_key] = {
            "issue_ids": issue_ids,
            "issue_years": issue_years,
            "issue_refs": issue_refs,
            "authority": AUTHORITY,
            "refreshed_at": observed_at,
            "collector_revision": COLLECTOR_REVISION,
            "evidence_ref": "data/provenance/expected-set-observations/elsevier-2026-browser.json",
            "source_url": source_url,
            "transport": str(observed.get("transport", "")).strip(),
        }

    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--journals-config", type=Path, default=Path("config/journals.yml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    state = build_state(_read_json(args.evidence), _load_journals(args.journals_config))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "journals": len(state["discovery"]),
        "authoritative_entries": sum(len(v["issue_ids"]) for v in state["discovery"].values()),
        "excluded_future_entries": len(state["expected_issue_exclusions"]),
        "observed_at": state["updated_at"],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
