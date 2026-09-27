from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
PHASE = "baseline_metrics_only"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _age_seconds(now: datetime, value: object) -> int | None:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def build_slo_metrics(
    completeness: dict[str, Any],
    monitoring: dict[str, Any],
    backfill_status: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or _iso_now()
    now = _parse_timestamp(generated)
    if now is None:
        raise ValueError("generated_at must be ISO-8601")

    journals = completeness.get("journals", [])
    if not isinstance(journals, list):
        raise ValueError("completeness.journals must be an array")

    measured = [
        item
        for item in journals
        if isinstance(item, dict)
        and str(item.get("status", "")) != "NOT_MEASURED"
        and str(item.get("measuredThrough", "")).strip()
    ]
    fresh = [
        item
        for item in measured
        if item.get("freshnessStatus") == "CURRENT_FOR_AUDIT_END"
    ]
    stale = [
        item
        for item in measured
        if item.get("freshnessStatus") == "STALE_FOR_AUDIT_END"
    ]
    not_measured = [
        item
        for item in journals
        if isinstance(item, dict) and item.get("status") == "NOT_MEASURED"
    ]
    measured_dates = sorted(
        str(item.get("measuredThrough", "")).strip()
        for item in measured
        if str(item.get("measuredThrough", "")).strip()
    )

    checks = monitoring.get("last_successful_checks", {})
    if not isinstance(checks, dict):
        checks = {}
    check_ages = [
        age
        for value in checks.values()
        if (age := _age_seconds(now, value)) is not None
    ]
    parsed_check_times = sorted(
        str(value)
        for value in checks.values()
        if _parse_timestamp(value) is not None
    )

    lifecycle = monitoring.get("lifecycle", {})
    if not isinstance(lifecycle, dict):
        lifecycle = {}
    authority_observations: list[tuple[str, datetime]] = []
    ready_latencies: list[int] = []
    for journal_id, raw in lifecycle.items():
        if not isinstance(raw, dict):
            continue
        observed_issue_id = str(raw.get("authority_observed_issue_id", "")).strip()
        observed_at = _parse_timestamp(raw.get("authority_observed_at"))
        if observed_issue_id and observed_at is not None:
            authority_observations.append((str(journal_id), observed_at))
        ready_issue_id = str(raw.get("canonical_ready_issue_id", "")).strip()
        ready_at = _parse_timestamp(raw.get("canonical_ready_at"))
        if (
            observed_issue_id
            and ready_issue_id == observed_issue_id
            and observed_at is not None
            and ready_at is not None
            and ready_at >= observed_at
        ):
            ready_latencies.append(int((ready_at - observed_at).total_seconds()))
    authority_observation_times = sorted(
        stamp.isoformat() for _journal_id, stamp in authority_observations
    )
    ready_latencies.sort()

    coverage = backfill_status.get("coverage", {})
    if not isinstance(coverage, dict):
        coverage = {}
    source_pending_count = int(coverage.get("source_pending", 0) or 0)
    missing_count = int(coverage.get("missing", 0) or 0)

    reconciliation = completeness.get("reconciliation", {})
    if not isinstance(reconciliation, dict):
        reconciliation = {}

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "generated_at": generated,
        "scope": "journals_tracked",
        "alerting_enabled": False,
        "thresholds_defined": False,
        "sources": {
            "completeness": "public/api/v1/completeness/2026.json",
            "monitoring": "public/api/v1/monitoring.json",
            "backfill_status": "public/api/v1/backfill-status.json",
        },
        "product_truth": {
            "journal_count": int(reconciliation.get("journal_count", len(journals)) or 0),
            "complete": int(reconciliation.get("complete", 0) or 0),
            "partial": int(reconciliation.get("partial", 0) or 0),
            "not_measured": int(reconciliation.get("not_measured", 0) or 0),
            "source_blocked": int(reconciliation.get("source_blocked", 0) or 0),
            "historical_publication_ready": int(
                coverage.get("publication_ready", 0) or 0
            ),
            "historical_missing": missing_count,
            "historical_source_pending": source_pending_count,
        },
        "metrics": {
            "expected_set_freshness": {
                "measurement_state": "direct",
                "semantic_scope": "authoritative expected-set observation freshness",
                "journal_count": len(journals),
                "measured_count": len(measured),
                "current_to_audit_end_count": len(fresh),
                "stale_for_audit_end_count": len(stale),
                "not_measured_count": len(not_measured),
                "oldest_measured_through": measured_dates[0] if measured_dates else "",
                "newest_measured_through": measured_dates[-1] if measured_dates else "",
                "authority_rule": (
                    "reuse completeness ledger authority classification; "
                    "candidate/static schedules do not establish freshness"
                ),
            },
            "monitor_probe_freshness": {
                "measurement_state": "direct",
                "semantic_scope": "monitor execution freshness only; not publisher authority",
                "configured_journal_count": int(
                    monitoring.get("summary", {}).get(
                        "configured_journals", len(checks)
                    )
                    or 0
                ),
                "successful_check_count": len(checks),
                "oldest_successful_check": (
                    parsed_check_times[0] if parsed_check_times else ""
                ),
                "newest_successful_check": (
                    parsed_check_times[-1] if parsed_check_times else ""
                ),
                "max_check_age_seconds": max(check_ages) if check_ages else None,
                "min_check_age_seconds": min(check_ages) if check_ages else None,
            },
            "current_issue_authority_freshness": {
                "measurement_state": (
                    "direct"
                    if journals and len(authority_observations) == len(journals)
                    else "partial"
                ),
                "measured_count": len(authority_observations),
                "journal_count": len(journals),
                "oldest_authority_observed_at": (
                    authority_observation_times[0]
                    if authority_observation_times
                    else ""
                ),
                "newest_authority_observed_at": (
                    authority_observation_times[-1]
                    if authority_observation_times
                    else ""
                ),
                "reason": (
                    "future first-party observations are persisted monotonically; "
                    "coverage remains partial until all tracked journals have a "
                    "durable authority observation for the current lifecycle"
                ),
                "do_not_substitute": [
                    "retrieved_at",
                    "last_checked_at",
                    "git_commit_time",
                ],
                "required_instrumentation": [
                    "authority_observed_at",
                    "authority_observed_issue_id",
                ],
            },
            "official_detection_to_canonical_ready_latency": {
                "measurement_state": (
                    "partial" if ready_latencies else "not_yet_measurable"
                ),
                "observed_transition_count": len(authority_observations),
                "ready_transition_count": len(ready_latencies),
                "min_latency_seconds": (
                    ready_latencies[0] if ready_latencies else None
                ),
                "max_latency_seconds": (
                    ready_latencies[-1] if ready_latencies else None
                ),
                "median_latency_seconds": (
                    ready_latencies[len(ready_latencies) // 2]
                    if ready_latencies
                    else None
                ),
                "reason": (
                    "latency is reported only when immutable first-party observation "
                    "and canonical-ready timestamps share the same issue identity"
                ),
                "required_instrumentation": [
                    "authority_observed_at",
                    "canonical_ready_at",
                    "issue_id",
                ],
            },
            "source_pending_age": {
                "measurement_state": "count_only",
                "open_count": source_pending_count,
                "age_measurable": False,
                "reason": (
                    "historical lifecycle now persists future source_pending_since "
                    "transitions, but the public coverage input does not yet expose "
                    "per-issue timestamps for age aggregation"
                ),
                "required_instrumentation": ["source_pending_since_public_rollup"],
            },
            "confirmed_missing_age": {
                "measurement_state": "count_only",
                "open_count": missing_count,
                "age_measurable": False,
                "reason": (
                    "current completeness/backfill surfaces identify missing issues but "
                    "do not persist the first confirmed-missing transition timestamp"
                ),
                "required_instrumentation": ["missing_since"],
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completeness", type=Path, required=True)
    parser.add_argument("--monitoring", type=Path, required=True)
    parser.add_argument("--backfill-status", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--generated-at", default="")
    args = parser.parse_args()

    payload = build_slo_metrics(
        _read_json(args.completeness),
        _read_json(args.monitoring),
        _read_json(args.backfill_status),
        generated_at=args.generated_at or None,
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "phase": payload["phase"],
                "alerting_enabled": payload["alerting_enabled"],
                "expected_set_measured": payload["metrics"][
                    "expected_set_freshness"
                ]["measured_count"],
                "source_pending": payload["metrics"]["source_pending_age"][
                    "open_count"
                ],
                "missing": payload["metrics"]["confirmed_missing_age"]["open_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
