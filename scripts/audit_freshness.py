from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.update_journals import issue_is_newer, select_display_issue

DEFAULT_API_ROOT = ROOT / "public" / "api" / "v1"
DEFAULT_STATE = ROOT / "data" / "monitoring" / "state.json"
CONFIG_PATH = ROOT / "config" / "journals.yml"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _quality_counts(issue: dict[str, Any]) -> tuple[int, int]:
    quality = issue.get("quality", {}) if isinstance(issue, dict) else {}
    return (
        int(quality.get("abstract_en_complete", 0) or 0),
        int(quality.get("translation_complete", 0) or 0),
    )


def _period_key(issue: dict[str, Any]) -> tuple[int, int]:
    text = str(issue.get("publication_date", "")).strip()
    # Reuse production chronology instead of interpreting free-form labels here.
    sentinel = {"volume": "0", "issue": "0", "publication_date": "1900-01-01"}
    if issue_is_newer(issue, sentinel):
        # `issue_is_newer` already normalizes month names, ISO dates and YYYY-MM.
        # Pull the comparable year/month through a tiny local parser solely for
        # equality diagnostics; chronology continues to use the production helper.
        from datetime import datetime
        import re

        for fmt in ("%B %Y", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.year, parsed.month
            except ValueError:
                continue
        match = re.search(r"(20\d{2})[^0-9]+(1[0-2]|0?[1-9])", text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return (0, 0)


def _finding(
    journal_id: str,
    code: str,
    severity: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "journal_id": journal_id,
        "code": code,
        "severity": severity,
        **details,
    }


def audit_journal_freshness(
    journal_id: str,
    ready: dict[str, Any] | None,
    detected: dict[str, Any] | None,
    monitor_entry: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Compare ready, detected and monitor layers without promoting any layer."""

    findings: list[dict[str, Any]] = []
    monitor_entry = monitor_entry or {}

    if ready and detected:
        ready_id = str(ready.get("issue_id", ""))
        detected_id = str(detected.get("issue_id", ""))
        if ready_id and detected_id and ready_id == detected_id:
            ready_period = _period_key(ready)
            detected_period = _period_key(detected)
            if (
                ready_period != (0, 0)
                and detected_period != (0, 0)
                and ready_period != detected_period
            ):
                findings.append(
                    _finding(
                        journal_id,
                        "same_issue_period_divergence",
                        "warning",
                        issue_id=ready_id,
                        ready_publication_date=str(ready.get("publication_date", "")),
                        detected_publication_date=str(
                            detected.get("publication_date", "")
                        ),
                    )
                )

            ready_abstracts, ready_translations = _quality_counts(ready)
            detected_abstracts, detected_translations = _quality_counts(detected)
            if (
                str(ready.get("publication_state", "")) == "ready"
                and str(detected.get("publication_state", "")) != "ready"
            ) or (
                detected_abstracts < ready_abstracts
                or detected_translations < ready_translations
            ):
                findings.append(
                    _finding(
                        journal_id,
                        "same_issue_detected_quality_regression",
                        "warning",
                        issue_id=ready_id,
                        ready_publication_state=str(
                            ready.get("publication_state", "")
                        ),
                        detected_publication_state=str(
                            detected.get("publication_state", "")
                        ),
                        ready_abstracts=ready_abstracts,
                        detected_abstracts=detected_abstracts,
                        ready_translations=ready_translations,
                        detected_translations=detected_translations,
                    )
                )
        elif ready_id and detected_id and issue_is_newer(ready, detected):
            findings.append(
                _finding(
                    journal_id,
                    "detected_older_than_ready",
                    "warning",
                    ready_issue_id=ready_id,
                    detected_issue_id=detected_id,
                    ready_publication_date=str(ready.get("publication_date", "")),
                    detected_publication_date=str(
                        detected.get("publication_date", "")
                    ),
                )
            )

    display: dict[str, Any] | None = None
    selected = select_display_issue(detected, ready)
    if selected == "detected":
        display = detected
    elif selected == "ready":
        display = ready

    candidate = monitor_entry.get("candidate")
    if isinstance(candidate, dict) and candidate and display:
        candidate_view = {
            "volume": str(candidate.get("volume", "")),
            "issue": str(candidate.get("issue", "")),
            "publication_date": str(candidate.get("publication_date", "")),
        }
        issue_key = str(candidate.get("issue_key", ""))
        display_key = f"{display.get('volume', '')}:{display.get('issue', '')}"
        if issue_key and issue_key != display_key and issue_is_newer(
            candidate_view, display
        ):
            findings.append(
                _finding(
                    journal_id,
                    "monitor_candidate_ahead",
                    "warning",
                    candidate_issue_key=issue_key,
                    display_issue_id=str(display.get("issue_id", "")),
                    monitor_status=str(monitor_entry.get("status", "")),
                    candidate_publication_date=str(
                        candidate.get("publication_date", "")
                    ),
                    candidate_doi_count=int(candidate.get("doi_count", 0) or 0),
                )
            )

    return findings


def audit_repository(
    *,
    api_root: Path = DEFAULT_API_ROOT,
    state_path: Path = DEFAULT_STATE,
) -> dict[str, Any]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["journals"]
    state = read_json(state_path) or {}
    monitor_journals = state.get("journals", {})
    findings: list[dict[str, Any]] = []
    audited = 0

    for key, journal in config.items():
        if not journal.get("enabled"):
            continue
        audited += 1
        journal_id = str(journal["id"])
        issue_root = api_root / "journals" / journal_id / "issues"
        ready = read_json(issue_root / "current.json")
        detected = read_json(issue_root / "detected.json")
        monitor = monitor_journals.get(key) or monitor_journals.get(journal_id) or {}
        findings.extend(
            audit_journal_freshness(journal_id, ready, detected, monitor)
        )

    severities = Counter(str(item.get("severity", "unknown")) for item in findings)
    codes = Counter(str(item.get("code", "unknown")) for item in findings)
    journals_with_findings = sorted(
        {str(item.get("journal_id", "")) for item in findings if item.get("journal_id")}
    )
    return {
        "schema_version": "1.0",
        "audited_journals": audited,
        "journals_with_findings": journals_with_findings,
        "summary": {
            "finding_count": len(findings),
            "journal_count_with_findings": len(journals_with_findings),
            "severities": dict(sorted(severities.items())),
            "codes": dict(sorted(codes.items())),
        },
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit reader freshness across ready, detected and monitor layers."
    )
    parser.add_argument("--api-root", type=Path, default=DEFAULT_API_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Return non-zero only for severity=error findings.",
    )
    args = parser.parse_args()

    report = audit_repository(api_root=args.api_root, state_path=args.state)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(rendered + "\n", encoding="utf-8")
    if args.fail_on_errors and report["summary"]["severities"].get("error", 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
