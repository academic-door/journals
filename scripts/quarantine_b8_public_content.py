"""Rights-safe containment of public B8 issue payloads and translation cache.

The public data branch may contain a newer unadjudicated publisher issue.
For rights-qualified B8 titles (excluding legacy JEGROWTH), *only* a
validated public metadata projection may leave a protected Actions job.
Restore the newest already accepted immutable projection when a moving
current/detected issue contains unauthorized content. Never invent a
license, translate a restricted abstract, or claim the newer issue ready.

This tool runs in the disposable Actions overlay; the normal serialized
data publisher owns any data-branch mutation. It does not push Git.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.b8_rights_qualified import MODE, validate_public_projection

# JEGROWTH was admitted under the pre-metadata-first contract: do not
# retroactively reinterpret its accepted public content.
RIGHTS_QUALIFIED_TITLES = {
    "JAE", "JOEG", "ITPF", "IJIO", "JFI", "JIMF", "EJPE",
}


def _safe_projection(document: Any) -> bool:
    if not isinstance(document, dict) or document.get("public_mode") != MODE:
        return False
    try:
        validate_public_projection(document)
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected public issue object: {path}")
    return value


def _sort_key(document: dict) -> tuple[int, int, str]:
    def numeric(value: object) -> int:
        import re
        match = re.search(r"\d+", str(value or ""))
        return int(match.group()) if match else -1

    return (numeric(document.get("volume")), numeric(document.get("issue")),
            str(document.get("publication_date", "")))


def contain_one(api_root: Path, journal_id: str) -> list[str]:
    """Repair only public B8 files; preserve every verified immutable record."""
    if journal_id not in {title.casefold() for title in RIGHTS_QUALIFIED_TITLES}:
        raise ValueError("outside B8 rights-qualified domain")
    folder = api_root / "journals" / journal_id / "issues"
    if not folder.exists():
        return []

    archive_candidates: list[tuple[Path, dict]] = []
    unsafe: list[Path] = []
    current = folder / "current.json"
    for path in sorted(folder.glob("*.json")):
        if path.name == "index.json":
            continue
        document = _read(path)
        if _safe_projection(document):
            if path.name not in {"current.json", "detected.json"}:
                archive_candidates.append((path, document))
        else:
            unsafe.append(path)

    # A protected B8 title must already have a real, independently
    # validated archived rights-qualified release. Do not create one here.
    if not archive_candidates:
        raise ValueError(f"{journal_id}: no accepted immutable rights-qualified issue")

    changed: list[str] = []
    if current in unsafe:
        accepted_path, _ = max(archive_candidates, key=lambda pair: _sort_key(pair[1]))
        current.write_bytes(accepted_path.read_bytes())
        changed.append(str(current.relative_to(api_root)))
        unsafe.remove(current)

    for path in unsafe:
        path.unlink()
        changed.append(str(path.relative_to(api_root)))

    if not current.exists() or not _safe_projection(_read(current)):
        raise ValueError(f"{journal_id}: no rights-qualified current issue after quarantine")
    for path in folder.glob("*.json"):
        if path.name != "index.json" and not _safe_projection(_read(path)):
            raise ValueError(f"{journal_id}: unreviewed public issue remains: {path.name}")
    return changed


def contain_release(root: Path) -> list[str]:
    manifest = json.loads(
        (root / "config/journal-expansion-b8.json").read_text(encoding="utf-8")
    )
    selected = [
        entry["id"] for entry in manifest["journals"]
        if entry["key"] in RIGHTS_QUALIFIED_TITLES
        and entry["rollout_status"] in {
            "production_verified", "activation_pending_production",
        }
    ]
    api = root / "public/api/v1"
    changed: list[str] = []
    for journal in selected:
        changed.extend(contain_one(api, journal))

        # A public Git repository is not a private translation staging area.
        # The rights-qualified B8 releases currently have no public grant for
        # cached translated derivatives; their private staging artifacts stay
        # separate from the public repo's production translation cache.
        cache = root / "data/translation-cache" / f"{journal}.json"
        if cache.exists():
            payload = _read(cache)
            if payload:
                cache.unlink()
                changed.append(str(cache.relative_to(root)))

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    changed = contain_release(root)
    if changed:
        # Recalculate every derived search/health/collection surface after
        # removing disallowed archived and moving-current records.
        subprocess.run(
            [sys.executable, "scripts/rebuild_public_snapshot.py"],
            cwd=root, check=True,
        )
        subprocess.run(
            [sys.executable, "scripts/publish_data_delta.py",
             "rebuild-archive-indexes", "--root", str(root)],
            cwd=root, check=True,
        )
    print(json.dumps({
        "rights_quarantine_paths": sorted(changed),
        "rights_qualified": True,
        "published_new_issue": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
