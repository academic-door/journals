from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path, PurePosixPath


ALLOWED_PATHS = (
    "public/api",
    "public/project-manifest.json",
    "public/backfill-status.md",
    "data/translation-cache",
    "data/backfill-state",
    "data/backfill-staging",
    "data/monitoring/state.json",
    "data/monitoring/email-notifications.json",
)
SNAPSHOT_METADATA = ".publish-data-snapshot.json"


class PublishConflict(RuntimeError):
    """The fresh data branch changed a path also changed by this run."""


def _relative_path(value: str) -> Path:
    normalized = PurePosixPath(value.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
        raise ValueError(f"unsafe publication path: {value}")
    candidate = normalized.as_posix().rstrip("/")
    if not any(
        candidate == allowed or candidate.startswith(f"{allowed}/")
        for allowed in ALLOWED_PATHS
    ):
        raise ValueError(f"path is outside the data-branch allowlist: {value}")
    return Path(*normalized.parts)


def _digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source: Path, target: Path) -> None:
    if source.is_symlink():
        raise ValueError(f"publication snapshots may not contain symlinks: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _is_excluded(relative: Path, patterns: list[str]) -> bool:
    posix = PurePosixPath(relative.as_posix())
    return any(posix.match(pattern) for pattern in patterns)


def _validate_excludes(patterns: list[str]) -> list[str]:
    for pattern in patterns:
        normalized = PurePosixPath(pattern.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError(f"unsafe exclusion pattern: {pattern}")
    return [pattern.replace("\\", "/") for pattern in patterns]


def _copy_selected(
    root: Path,
    output: Path,
    paths: list[Path],
    *,
    excludes: list[str] | None = None,
) -> None:
    excludes = excludes or []
    root = root.resolve()
    output = output.resolve()
    if output == root or root in output.parents:
        raise ValueError("snapshot output must be outside the source root")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for relative in paths:
        source = root / relative
        target = output / relative
        if _is_excluded(relative, excludes):
            continue
        if source.is_symlink():
            raise ValueError(f"publication snapshots may not contain symlinks: {source}")
        if source.is_file():
            _copy_file(source, target)
        elif source.is_dir():
            for file_path in source.rglob("*"):
                if file_path.is_symlink():
                    raise ValueError(
                        f"publication snapshots may not contain symlinks: {file_path}"
                    )
                if file_path.is_file():
                    file_relative = file_path.relative_to(root)
                    if not _is_excluded(file_relative, excludes):
                        _copy_file(file_path, output / file_relative)
    (output / SNAPSHOT_METADATA).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "paths": [path.as_posix() for path in paths],
                "excludes": excludes,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _files_below(
    root: Path,
    relative: Path,
    *,
    excludes: list[str] | None = None,
) -> set[Path]:
    excludes = excludes or []
    candidate = root / relative
    if candidate.is_file():
        return set() if _is_excluded(relative, excludes) else {relative}
    if not candidate.is_dir():
        return set()
    files: set[Path] = set()
    for path in candidate.rglob("*"):
        file_relative = path.relative_to(root)
        if (
            path.is_file()
            and not path.is_symlink()
            and not _is_excluded(file_relative, excludes)
        ):
            files.add(file_relative)
    return files


def apply_delta(
    *,
    baseline: Path,
    generated: Path,
    target: Path,
    paths: list[Path],
    excludes: list[str] | None = None,
) -> dict[str, list[str]]:
    """Apply only this run's file-level delta to a freshly fetched data tree.

    This is a three-way merge. Files unchanged between ``baseline`` and
    ``generated`` are never copied, so fresher unrelated data already in
    ``target`` survives. If both this run and the fresh branch changed the same
    file, publication stops instead of silently choosing a stale snapshot.
    """

    baseline = baseline.resolve()
    generated = generated.resolve()
    target = target.resolve()
    excludes = excludes or []
    changed: list[str] = []
    deleted: list[str] = []
    conflicts: list[str] = []

    candidates: set[Path] = set()
    for relative in paths:
        candidates.update(_files_below(baseline, relative, excludes=excludes))
        candidates.update(_files_below(generated, relative, excludes=excludes))

    for relative in sorted(candidates, key=lambda item: item.as_posix()):
        baseline_hash = _digest(baseline / relative)
        generated_hash = _digest(generated / relative)
        if baseline_hash == generated_hash:
            continue

        target_path = target / relative
        target_hash = _digest(target_path)
        if target_hash not in {baseline_hash, generated_hash}:
            conflicts.append(relative.as_posix())
            continue

        if generated_hash is None:
            if target_path.exists():
                target_path.unlink()
                deleted.append(relative.as_posix())
            continue

        if target_hash != generated_hash:
            _copy_file(generated / relative, target_path)
            changed.append(relative.as_posix())

    if conflicts:
        raise PublishConflict(
            "fresh data branch changed the same paths: " + ", ".join(conflicts)
        )
    return {"changed": changed, "deleted": deleted, "conflicts": conflicts}


def _paths_from_args(values: list[str]) -> list[Path]:
    if not values:
        raise ValueError("at least one --path is required")
    paths = [_relative_path(value) for value in values]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate publication paths are not allowed")
    return paths


def rebuild_archive_indexes(root: Path) -> None:
    """Rebuild per-journal history indexes from a freshly merged public tree."""

    root = root.resolve()
    if not (root / "scripts" / "update_journals.py").is_file():
        raise ValueError(f"repository scripts are missing below {root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    import yaml

    from scripts.update_journals import now_iso, write_archive_index

    config = yaml.safe_load(
        (root / "config" / "journals.yml").read_text(encoding="utf-8")
    )["journals"]
    api_root = root / "public" / "api" / "v1"
    updated_at = now_iso()
    rebuilt = 0
    for journal in config.values():
        if not journal.get("enabled"):
            continue
        write_archive_index(
            journal["id"],
            journal["name"],
            updated_at=updated_at,
            api_root=api_root,
        )
        rebuilt += 1
    print(f"archive indexes rebuilt: {rebuilt}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot and safely apply an allowlisted data-branch delta."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--root", type=Path, required=True)
    snapshot_parser.add_argument("--output", type=Path, required=True)
    snapshot_parser.add_argument("--path", action="append", default=[])
    snapshot_parser.add_argument("--exclude", action="append", default=[])

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--baseline", type=Path, required=True)
    apply_parser.add_argument("--generated", type=Path, required=True)
    apply_parser.add_argument("--target", type=Path, required=True)
    apply_parser.add_argument("--path", action="append", default=[])
    apply_parser.add_argument("--exclude", action="append", default=[])
    apply_parser.add_argument("--report", type=Path)

    rebuild_parser = subparsers.add_parser("rebuild-archive-indexes")
    rebuild_parser.add_argument("--root", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "rebuild-archive-indexes":
        rebuild_archive_indexes(args.root)
        return 0
    paths = _paths_from_args(args.path)
    excludes = _validate_excludes(args.exclude)
    if args.command == "snapshot":
        _copy_selected(args.root, args.output, paths, excludes=excludes)
        print(f"publication snapshot: {len(paths)} allowlisted roots")
        return 0

    try:
        report = apply_delta(
            baseline=args.baseline,
            generated=args.generated,
            target=args.target,
            paths=paths,
            excludes=excludes,
        )
    except PublishConflict as error:
        print(f"publication conflict: {error}")
        return 2
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        "publication delta applied: "
        f"{len(report['changed'])} changed, {len(report['deleted'])} deleted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
