"""Canonicalize and compare complete strict-audit failures across a batch."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

DOI_RE = re.compile(r"^(10\.\S+?):\s*(.*)$")


def canonical_failure(line: str, *, doi_to_issue: dict[str, str] | None = None) -> dict[str, Any] | None:
    raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
    # ``audit_public_data.py`` emits a success summary on stdout.  Only its
    # explicit ``FAIL `` records are audit findings; treating any other line
    # as a failure makes a clean audit fail the incremental gate.
    if not raw.startswith("FAIL "):
        return None
    raw = raw[5:].strip()
    if not raw:
        return None
    scope, separator, remainder = raw.partition(":")
    identity = ""
    reason = remainder.strip() if separator else raw
    match = DOI_RE.match(reason)
    if match:
        identity = match.group(1).casefold()
        reason = match.group(2).strip()
    normalized_reason = re.sub(r"\s+", " ", reason).strip().casefold()
    return {
        "audit": "public_data_strict",
        "issue_id": (doi_to_issue or {}).get(identity, ""),
        "identity": identity,
        "error_type": normalized_reason.split(":", 1)[0].strip(),
        "normalized_reason": normalized_reason,
        "severity": "error",
        "scope": "article" if identity else scope.casefold(),
        "exact_failure": raw,
    }


def _key(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(item.get(field, "")) for field in ("audit", "issue_id", "identity", "error_type", "normalized_reason"))


def canonicalize_failures(lines: Iterable[str], *, doi_to_issue: dict[str, str] | None = None) -> list[dict[str, Any]]:
    values = [item for line in lines if (item := canonical_failure(line, doi_to_issue=doi_to_issue))]
    return sorted(values, key=_key)


def compare_audit_logs(before: Iterable[str], after: Iterable[str], *, doi_to_issue: dict[str, str] | None = None, publish_issue_ids: set[str] | None = None) -> dict[str, Any]:
    before_values = canonicalize_failures(before, doi_to_issue=doi_to_issue)
    after_values = canonicalize_failures(after, doi_to_issue=doi_to_issue)
    before_by_key = {_key(item): item for item in before_values}
    after_by_key = {_key(item): item for item in after_values}
    before_identity = {
        (item["audit"], item["issue_id"], item["identity"], item["error_type"]): item
        for item in before_values
    }
    worsened = []
    for item in after_values:
        identity = (item["audit"], item["issue_id"], item["identity"], item["error_type"])
        old = before_identity.get(identity)
        if old and _key(old) != _key(item):
            worsened.append({"before": old, "after": item})
    worsened_after_keys = {_key(item["after"]) for item in worsened}
    new = [
        item
        for key, item in after_by_key.items()
        if key not in before_by_key and key not in worsened_after_keys
    ]
    preserved = [item for key, item in after_by_key.items() if key in before_by_key]
    candidate = [item for item in after_values if publish_issue_ids and item.get("issue_id") in publish_issue_ids]
    return {
        "ok": not new and not worsened and not candidate,
        "baseline_strict_failures": before_values,
        "candidate_strict_failures": after_values,
        "preserved_baseline_failures": preserved,
        "new_failures": sorted(new, key=_key),
        "worsened_failures": sorted(worsened, key=lambda item: _key(item["after"])),
        "resolved_failures": [item for key, item in before_by_key.items() if key not in after_by_key],
        "candidate_issue_failures": candidate,
    }


def _doi_map(api_root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in api_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            for article in payload.get("articles", []):
                if isinstance(article, dict) and article.get("doi") and payload.get("issue_id"):
                    mapping[str(article["doi"]).strip().casefold()] = str(payload["issue_id"])
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--api-root", type=Path)
    parser.add_argument("--publish-issue-ids", default="")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = compare_audit_logs(
        args.before.read_text(encoding="utf-8").splitlines(),
        args.after.read_text(encoding="utf-8").splitlines(),
        doi_to_issue=_doi_map(args.api_root) if args.api_root else {},
        publish_issue_ids={value.strip() for value in args.publish_issue_ids.split(",") if value.strip()},
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
