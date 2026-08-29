"""Classify the public unresolved issue set without recollecting sources."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


CATEGORIES = (
    "READY_CANDIDATE",
    "TRANSLATION_FIX",
    "SOURCE_RECOVERABLE",
    "BROWSER_REQUIRED",
    "EXTERNAL_BLOCKED",
    "PIPELINE_BLOCKED",
)


def _classify(record: dict[str, Any]) -> str:
    category = str(record.get("category", ""))
    if category == "ready":
        return "READY_CANDIDATE"
    if category == "translation_required":
        return "TRANSLATION_FIX"
    if category == "recoverable":
        return "SOURCE_RECOVERABLE"
    if category == "browser_required":
        return "BROWSER_REQUIRED"
    if category != "source_pending":
        return "PIPELINE_BLOCKED"

    collector = str(record.get("collector", "")).casefold()
    retry_class = str(record.get("retry_class", "")).casefold()
    reason = str(record.get("reason", ""))
    if retry_class == "source" and collector in {
        "chicago",
        "elsevier",
        "oup",
        "wiley",
    }:
        return "SOURCE_RECOVERABLE"
    if "HTTPError: 403" in reason:
        return "BROWSER_REQUIRED"
    return "EXTERNAL_BLOCKED"


def classify(public_status: dict[str, Any], candidate_gap: dict[str, Any]) -> dict[str, Any]:
    coverage = public_status.get("coverage", {})
    public_ids = list(dict.fromkeys(
        [*coverage.get("missing_issue_ids", []), *coverage.get("source_pending_issue_ids", [])]
    ))
    public_id_set = set(public_ids)
    candidate_by_id = {
        str(record.get("issue_id")): record
        for record in candidate_gap.get("records", [])
        if isinstance(record, dict) and record.get("issue_id") in public_id_set
    }
    if len(public_ids) != int(coverage.get("missing", 0)) + int(coverage.get("source_pending", 0)):
        raise ValueError("public unresolved IDs are not unique")
    if set(candidate_by_id) != public_id_set:
        missing = sorted(public_id_set - set(candidate_by_id))
        raise ValueError(f"candidate gap manifest missing public IDs: {missing}")

    grouped: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    details: dict[str, dict[str, Any]] = {}
    for issue_id in public_ids:
        record = candidate_by_id[issue_id]
        closeout_category = _classify(record)
        grouped[closeout_category].append(issue_id)
        details[issue_id] = {
            "issue_id": issue_id,
            "journal": record.get("journal", ""),
            "candidate_category": record.get("category", ""),
            "closeout_category": closeout_category,
            "reason": record.get("reason", ""),
            "retry_class": record.get("retry_class", ""),
            "collector": record.get("collector", ""),
            "official_url": record.get("official_url", ""),
            "next_action": record.get("next_action", ""),
            "last_error_code": record.get("last_error_code", ""),
        }
    for values in grouped.values():
        values.sort()
    return {
        "schema_version": "1.0",
        "baseline": {
            "updated_at": public_status.get("updated_at", ""),
            "ready": public_status.get("summary", {}).get("complete", 0),
            "missing": coverage.get("missing", 0),
            "source_pending": coverage.get("source_pending", 0),
            "unresolved": len(public_ids),
        },
        "candidate_manifest": {
            "generated_at": candidate_gap.get("generated_at", ""),
            "record_count": len(candidate_gap.get("records", [])),
            "matched_public_unresolved": len(candidate_by_id),
        },
        "counts": {category: len(grouped[category]) for category in CATEGORIES},
        "issue_ids": grouped,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-status", type=Path, required=True)
    parser.add_argument("--candidate-gap", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    public_status = json.loads(args.public_status.read_text(encoding="utf-8"))
    candidate_gap = json.loads(args.candidate_gap.read_text(encoding="utf-8"))
    payload = classify(public_status, candidate_gap)
    if sum(payload["counts"].values()) != payload["baseline"]["unresolved"]:
        raise ValueError("closeout categories do not sum to unresolved baseline")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
