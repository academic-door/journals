"""Persist canonical strict-audit findings as baseline debt."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_incremental_audit import _doi_map, canonicalize_failures


def update_debt(log_path: Path, api_root: Path, output: Path) -> dict:
    existing = {}
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    old_entries = {str(item.get("baseline_reference")): item for item in existing.get("failures", [])}
    doi_to_issue = _doi_map(api_root)
    title_by_doi: dict[str, str] = {}
    for path in api_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            for article in payload.get("articles", []):
                if isinstance(article, dict) and article.get("doi"):
                    title_by_doi[str(article["doi"]).strip().casefold()] = str(article.get("title_en", ""))
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    failures = []
    for item in canonicalize_failures(log_path.read_text(encoding="utf-8").splitlines(), doi_to_issue=doi_to_issue):
        reference = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:16]
        prior = old_entries.get(reference, {})
        failures.append({
            "issue_id": item.get("issue_id", ""),
            "doi": item.get("identity", ""),
            "title_en": title_by_doi.get(item.get("identity", ""), ""),
            "audit": item.get("audit", ""),
            "exact_failure": item.get("exact_failure", ""),
            "error_type": item.get("error_type", ""),
            "normalized_reason": item.get("normalized_reason", ""),
            "severity": item.get("severity", "error"),
            "scope": item.get("scope", ""),
            "baseline_reference": reference,
            "first_seen": prior.get("first_seen", now),
            "current_status": "baseline_open",
            "remediation_class": "translation_fix" if "translation changed numeric values" in item.get("normalized_reason", "") else "audit_reconciliation",
        })
    payload = {"schema_version": "1.0", "updated_at": now, "failures": failures}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"baseline_failures": len(failures), "output": str(output)}, ensure_ascii=False))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--api-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    update_debt(args.log, args.api_root, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
