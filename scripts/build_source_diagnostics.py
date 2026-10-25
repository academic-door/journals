"""Explain current journal source transport separately from saved publisher completeness.

Read-only, deterministic from the current public snapshot and existing immutable
official captures. No network calls, credentials, publisher access or archive writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
# Direct `python scripts/build_source_diagnostics.py` runs with scripts/ on
# sys.path; package imports need the owner repository root as well.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.publisher_coverage_sentinel import reconcile
PILOT = {"jeem", "jie", "energy"}


def _transport_class(primary: bool, quality: dict[str, Any]) -> str:
    if primary:
        return "primary_available"
    transport = str(quality.get("roster_transport") or "").lower()
    flags = set(quality.get("flags") or [])
    if transport == "sciencedirect-rss":
        return "publisher_rss_fallback"
    if "repec" in transport or "repec_publisher_supplied_roster" in flags:
        return "publisher_supplied_repec_fallback"
    if (quality.get("roster_match_scope") == "official-publisher-issue-full"
            and quality.get("official_roster_evidence")):
        return "stored_first_party_snapshot_primary_unavailable"
    return "other_primary_unavailable"


def build_report(
    configs: dict[str, Any],
    issues: dict[str, dict[str, Any]],
    health: dict[str, Any],
    roster_evidence: dict[str, dict[str, Any] | None],
    *,
    generated_at: str,
) -> dict[str, Any]:
    checks = health.get("checks") or {}
    items: list[dict[str, Any]] = []
    for key, cfg in configs.items():
        if not cfg.get("enabled"):
            continue
        jid = str(cfg["id"])
        issue = issues[jid]
        quality = issue.get("quality") or {}
        primary_key = f"{jid}_primary_transport"
        if primary_key not in checks or not isinstance(checks[primary_key], bool):
            raise ValueError(f"{primary_key}: source-health check missing")
        primary = checks[primary_key]
        capture = quality.get("official_roster_evidence") or {}
        path = str(cfg.get("official_roster_evidence_path") or "")
        publisher = roster_evidence.get(jid)
        coverage = None
        evidence_status = "not_automatically_checked"
        if jid in PILOT and path:
            if publisher is None:
                evidence_status = "verification_due"
            elif str(publisher.get("issue_id") or "").lower() != str(issue.get("issue_id") or "").lower():
                # An immutable roster of the PREVIOUS issue cannot certify the
                # new issue, regardless of the file's actual capture date.
                evidence_status = "verification_due"
            else:
                try:
                    coverage = reconcile(publisher, issue)
                    evidence_status = coverage["publisher_coverage"]
                except (ValueError, TypeError, KeyError):
                    evidence_status = "verification_blocked"
        items.append({
            "journal_id": jid,
            "issue_id": str(issue.get("issue_id") or ""),
            "archive_ready": (
                issue.get("publication_state") == "ready"
                and issue.get("content_status") == "complete"
            ),
            "primary_transport_available": primary,
            "source_route": _transport_class(primary, quality),
            "roster_transport": quality.get("roster_transport"),
            "roster_authority": quality.get("roster_authority"),
            "roster_match_scope": quality.get("roster_match_scope"),
            "saved_official_capture_at": capture.get("captured_at"),
            "saved_official_capture_url": capture.get("official_url"),
            "publisher_coverage": evidence_status,
            # Do not confuse a genuinely observed publisher capture with the
            # report generation time, which is NOT a publisher observation.
            "publisher_observed_at": coverage.get("observed_at") if coverage else None,
            "last_verified_at": coverage.get("last_verified_at") if coverage else None,
            "publisher_difference": coverage.get("diff") if coverage else None,
            "publisher_verification_errors": coverage.get("verification_errors") if coverage else [],
        })
    routes = Counter(item["source_route"] for item in items)
    statuses = Counter(item["publisher_coverage"] for item in items)
    return {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "source": "current_owner_immutable_evidence_and_public_health",
        "scope": "all_current_journals; strict_saved_publisher_comparator_pilot_jeem_jie_energy",
        "status": "observational",
        "summary": {
            "journals": len(items),
            "primary_available": sum(x["primary_transport_available"] for x in items),
            "primary_unavailable": sum(not x["primary_transport_available"] for x in items),
            "source_route_counts": dict(sorted(routes.items())),
            "publisher_coverage_counts": dict(sorted(statuses.items())),
        },
        "journals": items,
    }


def build_from_tree(root: Path, api_root: Path, *, generated_at: str) -> dict[str, Any]:
    cfg = yaml.safe_load((root / "config/journals.yml").read_text(encoding="utf-8"))["journals"]
    health = json.loads((api_root / "health.json").read_text(encoding="utf-8"))
    issues: dict[str, dict[str, Any]] = {}
    rosters: dict[str, dict[str, Any] | None] = {}
    for name, config in cfg.items():
        if not config.get("enabled"):
            continue
        jid = str(config["id"])
        path = api_root / "journals" / jid / "issues" / "current.json"
        issues[jid] = json.loads(path.read_text(encoding="utf-8"))
        evidence_path = config.get("official_roster_evidence_path")
        if jid in PILOT and evidence_path:
            source = (root / str(evidence_path)).resolve()
            if not source.is_relative_to(root.resolve()):
                raise ValueError(f"{name}: official evidence escapes owner root")
            rosters[jid] = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else None
    return build_report(cfg, issues, health, rosters, generated_at=generated_at)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api-root", type=Path, default=ROOT / "public/api/v1")
    ap.add_argument("--out", type=Path, default=ROOT / "public/api/v1/source-diagnostics.json")
    args = ap.parse_args()
    report = build_from_tree(
        ROOT, args.api_root,
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
