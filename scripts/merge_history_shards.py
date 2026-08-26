"""Merge isolated history-sprint artifacts into one last-known-good tree."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _publication_rank(payload: dict[str, Any]) -> tuple[int, int, int]:
    """Rank an issue payload so an old shard cannot regress published truth."""

    state_rank = {
        "ready": 4,
        "translation_partial": 3,
        "source_pending": 2,
        "enriching": 1,
        "blocked": 0,
    }
    publication_state = payload.get("publication_state") or payload.get("status") or "blocked"
    return (
        state_rank.get(str(publication_state), 0),
        int(payload.get("content_status") == "complete"),
        int(payload.get("source_status") in {"official_verified", "publisher_verified"}),
    )


def _attempt_stamp(payload: dict[str, Any]) -> str:
    return str(payload.get("last_attempt_at") or payload.get("updated_at") or "")


def merge_state(
    base: dict[str, Any], shard: dict[str, Any], shard_journals: set[str]
) -> dict[str, Any]:
    """Overlay only shard changes, preserving unrelated journals."""

    merged = json.loads(json.dumps(base, ensure_ascii=False))
    shard_issues = shard.get("issues") if isinstance(shard.get("issues"), dict) else {}
    merged.setdefault("issues", {})
    for issue_id, entry in shard_issues.items():
        if str(entry.get("journal", "")) in shard_journals:
            existing = merged["issues"].get(issue_id)
            if isinstance(existing, dict) and isinstance(entry, dict):
                existing_rank = _publication_rank(existing)
                incoming_rank = _publication_rank(entry)
                if existing_rank > incoming_rank or (
                    existing_rank == incoming_rank
                    and _attempt_stamp(existing)
                    and _attempt_stamp(existing) >= _attempt_stamp(entry)
                ):
                    continue
            merged["issues"][issue_id] = entry

    base_discovery = base.get("discovery") if isinstance(base.get("discovery"), dict) else {}
    shard_discovery = shard.get("discovery") if isinstance(shard.get("discovery"), dict) else {}
    merged.setdefault("discovery", {})
    for journal, snapshot in shard_discovery.items():
        if str(journal) in shard_journals:
            merged["discovery"][journal] = snapshot

    shard_rotation = shard.get("rotation") if isinstance(shard.get("rotation"), dict) else {}
    merged.setdefault("rotation", {})
    for key, values in shard_rotation.items():
        if not isinstance(values, dict):
            merged["rotation"][key] = values
            continue
        target = merged["rotation"].setdefault(key, {})
        for journal, value in values.items():
            if str(journal) in shard_journals:
                target[journal] = value
    merged["updated_at"] = shard.get("updated_at", merged.get("updated_at", ""))
    merged["schema_version"] = shard.get("schema_version", merged.get("schema_version", "1.1"))
    return merged


def copy_tree_overlay(source: Path, target: Path) -> None:
    if not source.exists():
        return
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Shards are intentionally resumable and can be older than the
            # latest data baseline.  Preserve a stronger issue archive when
            # an old shard only contains a provisional/blocked copy; indexes
            # and non-issue files are rebuilt later in the workflow.
            is_issue_archive = (
                "public" in destination.parts
                and "api" in destination.parts
                and "v1" in destination.parts
                and "journals" in destination.parts
                and destination.parent.name == "issues"
            )
            if is_issue_archive and destination.name != "index.json" and destination.suffix == ".json" and destination.exists():
                try:
                    existing = load_json(destination, {})
                    incoming = load_json(item, {})
                    if (
                        isinstance(existing, dict)
                        and isinstance(incoming, dict)
                        and _publication_rank(existing) > _publication_rank(incoming)
                    ):
                        continue
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    pass
            shutil.copy2(item, destination)


def merge_shards(root: Path, shards_root: Path) -> list[str]:
    shard_paths = sorted(path for path in shards_root.iterdir() if path.is_dir())
    if not shard_paths:
        raise ValueError(f"no shard artifacts found under {shards_root}")
    merged_reports: list[str] = []
    state_dir = root / "data" / "backfill-state"
    for shard in shard_paths:
        metadata = load_json(shard / "output" / "shard-metadata.json", {})
        shard_journals = {
            str(value)
            for value in (metadata.get("journals") or [])
            if str(value)
        }
        if not shard_journals:
            raise ValueError(f"missing shard metadata: {shard}")
        copy_tree_overlay(shard / "public" / "api" / "v1" / "journals", root / "public" / "api" / "v1" / "journals")
        copy_tree_overlay(shard / "data" / "backfill-staging", root / "data" / "backfill-staging")
        copy_tree_overlay(
            shard / "data" / "provenance" / "official-rosters",
            root / "data" / "provenance" / "official-rosters",
        )
        cache_source = shard / "data" / "translation-cache"
        cache_target = root / "data" / "translation-cache"
        if cache_source.exists():
            for cache_file in cache_source.glob("*.json"):
                merged_cache = load_json(cache_target / cache_file.name, {})
                shard_cache = load_json(cache_file, {})
                if isinstance(merged_cache, dict) and isinstance(shard_cache, dict):
                    merged_cache.update(shard_cache)
                    write_json(cache_target / cache_file.name, merged_cache)
                else:
                    shutil.copy2(cache_file, cache_target / cache_file.name)

        for state_file in (shard / "data" / "backfill-state").glob("*.json"):
            target = state_dir / state_file.name
            write_json(
                target,
                merge_state(
                    load_json(target, {}),
                    load_json(state_file, {}),
                    shard_journals,
                ),
            )

        report_dir = shard / "output"
        for report in report_dir.glob("*.json"):
            merged_reports.append(str(report))
    return merged_reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--shards-root", required=True)
    args = parser.parse_args()
    reports = merge_shards(Path(args.root), Path(args.shards_root))
    print(json.dumps({"shards": len(set(Path(path).parent for path in reports)), "reports": reports}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
