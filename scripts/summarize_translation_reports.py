"""Summarize exact translation shard reports for the integrator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(shards_root: Path) -> dict[str, object]:
    issue_ids: set[str] = set()
    model_calls = 0
    cache_reuses = 0
    failed_issue_ids: set[str] = set()
    for report_path in sorted(shards_root.glob("*/output/translation-fix.json")):
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        issue_ids.update(str(value) for value in payload.get("publishable_issue_ids", []))
        failed_issue_ids.update(str(value) for value in payload.get("failed_issue_ids", []))
        model_calls += int(payload.get("translation_model_calls", 0) or 0)
        cache_reuses += int(payload.get("translation_cache_reuses", 0) or 0)
    return {
        "publishable_issue_ids": sorted(issue_ids),
        "failed_issue_ids": sorted(failed_issue_ids),
        "translation_model_calls": model_calls,
        "translation_cache_reuses": cache_reuses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = summarize(args.shards_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(",".join(payload["publishable_issue_ids"]), payload["translation_model_calls"], payload["translation_cache_reuses"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
