"""Build deterministic, short historical-recovery shards from the gap manifest."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CATEGORIES = (
    "recoverable,translation_required,source_pending,browser_required"
)
MAX_CHUNK_SIZE = 12


def _adapter(record: dict[str, Any], collector: str) -> str:
    category = str(record.get("category", ""))
    journal = str(record.get("journal", "")).upper()
    official_url = str(record.get("official_url", ""))
    if "link.springer.com" in official_url and category in {
        "recoverable",
        "source_pending",
    }:
        return "springer-evidence"
    if "www.cambridge.org" in official_url and category == "source_pending":
        return "cambridge-evidence"
    if category == "translation_required":
        return "translation"
    if category == "browser_required":
        return "browser"
    if category == "source_pending":
        if collector == "aea":
            return "aea-evidence"
        if collector == "wiley":
            return "wiley-evidence"
        if journal == "LANDECON":
            return "highwire-evidence"
        if collector in {"oup", "chicago", "repec"}:
            # These collectors read an exact official issue/serial page. An
            # exact recollection is the evidence adapter and avoids inventing
            # a second parser with different roster semantics.
            return f"collect-{collector}"
        if collector in {"elsevier", "crossref"}:
            return "browser"
        return "official-evidence"
    return f"collect-{collector or 'other'}"


def build_queue(
    manifest: dict[str, Any],
    journals: dict[str, Any],
    *,
    categories: set[str],
    chunk_size: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not 1 <= chunk_size <= MAX_CHUNK_SIZE:
        raise ValueError(f"chunk_size must be between 1 and {MAX_CHUNK_SIZE}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in manifest.get("records", []):
        if not isinstance(raw, dict):
            continue
        issue_id = str(raw.get("issue_id", "")).strip()
        category = str(raw.get("category", "")).strip()
        journal = str(raw.get("journal", "")).strip()
        if not issue_id or category not in categories or not journal:
            continue
        collector = str((journals.get(journal) or {}).get("collector", "other"))
        record = dict(raw)
        record["collector"] = collector
        record["action"] = _adapter(record, collector)
        grouped[record["action"]].append(record)

    include: list[dict[str, Any]] = []
    shard_manifests: list[dict[str, Any]] = []
    for action in sorted(grouped):
        records = sorted(
            grouped[action],
            key=lambda item: (
                str(item.get("journal", "")),
                int(item.get("year") or 0),
                str(item.get("issue_id", "")),
            ),
        )
        for offset in range(0, len(records), chunk_size):
            chunk = records[offset : offset + chunk_size]
            shard = f"{action}-{offset // chunk_size + 1:03d}"
            issue_ids = [str(item["issue_id"]) for item in chunk]
            journal_keys = sorted({str(item["journal"]) for item in chunk})
            include.append(
                {
                    "shard": shard,
                    "action": action,
                    "issue_ids": ",".join(issue_ids),
                    "journals": ",".join(journal_keys),
                }
            )
            shard_manifests.append(
                {
                    "schema_version": "1.0",
                    "generated_at": manifest.get("generated_at", ""),
                    "summary": dict(Counter(item["category"] for item in chunk)),
                    "shard": shard,
                    "action": action,
                    "issue_ids": issue_ids,
                    "records": chunk,
                }
            )
    return {"include": include}, shard_manifests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap-manifest", type=Path, required=True)
    parser.add_argument("--journals-config", type=Path, default=Path("config/journals.yml"))
    parser.add_argument("--categories", default=DEFAULT_CATEGORIES)
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.gap_manifest.read_text(encoding="utf-8"))
    journals = yaml.safe_load(args.journals_config.read_text(encoding="utf-8"))["journals"]
    categories = {value.strip() for value in args.categories.split(",") if value.strip()}
    matrix, shards = build_queue(
        manifest,
        journals,
        categories=categories,
        chunk_size=args.chunk_size,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for shard in shards:
        (args.output_dir / f"{shard['shard']}.json").write_text(
            json.dumps(shard, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    matrix_json = json.dumps(matrix, separators=(",", ":"))
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"matrix={matrix_json}\n")
            handle.write(f"has_work={'true' if matrix['include'] else 'false'}\n")
            handle.write(f"issue_count={sum(len(item['issue_ids']) for item in shards)}\n")
    print(matrix_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
