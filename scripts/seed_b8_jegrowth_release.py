"""One-title B8 provenance-attested data seed; never activates JEGROWTH.

Consumes immutable successful Actions 40326883547 (artifact 12652145198),
validates official DOI/order, English/CN and semantic gates, and writes only
the ready current + archive issue under a caller-supplied data worktree.
The existing single-writer Actions job owns the eventual Git push.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile

import requests

from collectors.article_types import translation_is_complete
from scripts.b8_springer_pilot import MANIFEST, FIXTURE, approved_pilot, verify_capture
from scripts.translate_issue import _source_hash
from scripts.update_journals import (
    is_archivable_snapshot,
    issue_translation_semantics_valid,
    normalize_issue_content,
    validate_issue,
)

REPOSITORY = "academic-door/journals"
RUN_ID = 40326883547
ARTIFACT_ID = 12652145198
ARTIFACT_ZIP_SHA256 = "a215ed8fff65bc7157a7f47fd2f1ce5d794b4626b2d165c098791da47368e812"
FILES = frozenset({
    "jegrowth-31-3.candidate.json",
    "jegrowth-31-3.official-roster.json",
    "report.json",
})
EXPECTED_DOIS = (
    "10.1007/s10887-026-09265-x",
    "10.1007/s10887-026-09267-9",
    "10.1007/s10887-026-09266-w",
)


def download_attested_zip(token: str) -> bytes:
    if not token:
        raise ValueError("Actions artifact read token missing")
    url = f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{ARTIFACT_ID}/zip"
    response = requests.get(
        url, timeout=45, allow_redirects=True,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    response.raise_for_status()
    if len(response.content) > 100_000:
        raise ValueError("unusually large Actions attestation archive")
    return response.content


def verified_issue(artifact_zip: bytes, *, expected_sha: str = ARTIFACT_ZIP_SHA256) -> dict:
    observed = hashlib.sha256(artifact_zip).hexdigest()
    if observed != expected_sha:
        raise ValueError("B8 Actions artifact ZIP digest mismatch")
    with ZipFile(io.BytesIO(artifact_zip)) as z:
        if set(z.namelist()) != FILES:
            raise ValueError("B8 artifact contains an unexpected file set")
        if any(z.getinfo(name).file_size > 45_000 for name in FILES):
            raise ValueError("B8 artifact payload exceeds audited size")
        candidate = json.loads(z.read("jegrowth-31-3.candidate.json"))
        evidence = json.loads(z.read("jegrowth-31-3.official-roster.json"))
        report = json.loads(z.read("report.json"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stage = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _, source = approved_pilot(stage, manifest)
    verify_capture(evidence, source)
    if tuple(report.get("official_dois", [])) != EXPECTED_DOIS:
        raise ValueError("source report DOI order drift")
    if report.get("issue_id") != "jegrowth-31-3" or not report.get("quality_ready"):
        raise ValueError("pilot did not independently accept READY")
    if report.get("live_promoted") is not False:
        raise ValueError("artifact is not isolated source-only candidate")
    if report.get("source_status") != "official_verified" or report.get("publication_state") != "ready":
        raise ValueError("source or publication state gate failed")
    if candidate.get("journal_id") != "jegrowth" or candidate.get("issue_id") != "jegrowth-31-3":
        raise ValueError("candidate identity drift")
    normalized = normalize_issue_content(copy.deepcopy(candidate))
    validate_issue(normalized)
    if normalized != candidate:
        raise ValueError("candidate is not deterministically normalized")
    if candidate.get("source_status") != "official_verified" or candidate.get("content_status") != "complete" or candidate.get("publication_state") != "ready":
        raise ValueError("candidate readiness state mismatch")
    if not is_archivable_snapshot(candidate) or not issue_translation_semantics_valid(candidate):
        raise ValueError("candidate content or numeric translation is not publication-ready")
    if candidate.get("quality", {}).get("flags"):
        raise ValueError("candidate quality flags are not empty")
    if candidate.get("research_article_count") != 3 or len(candidate.get("articles", [])) != 3:
        raise ValueError("candidate article count drift")
    if tuple(x.get("doi") for x in candidate["articles"]) != EXPECTED_DOIS:
        raise ValueError("candidate DOI order drift")
    for row, original in zip(candidate["articles"], evidence["items"], strict=True):
        if row.get("doi") != original.get("doi") or row.get("authors") != original.get("authors"):
            raise ValueError("candidate source DOI/author differs from publisher")
        if row.get("title_en") != original.get("title_en"):
            raise ValueError("candidate source title differs from publisher")
        if not row.get("abstract_en") or not original.get("abstract_en"):
            raise ValueError("publisher abstract missing")
        if not translation_is_complete(row):
            raise ValueError("Chinese translation missing")
        translation = row.get("translation", {})
        if translation.get("source_hash") != _source_hash(row):
            raise ValueError("translated source digest differs from article")
        if translation.get("provider") != "deepseek" or translation.get("status") != "complete":
            raise ValueError("translation audit provenance missing")
    return candidate


def seed_only_if_absent(data_root: Path, candidate: dict) -> list[str]:
    relative = Path("public/api/v1/journals/jegrowth/issues")
    folder = data_root / relative
    target_paths = [folder / "current.json", folder / "jegrowth-31-3.json"]
    serialized = json.dumps(candidate, ensure_ascii=False, indent=2) + "\n"
    for target in target_paths:
        if target.exists() and target.read_text(encoding="utf-8") != serialized:
            raise ValueError(f"existing production payload differs: {target.name}")
    created = []
    for target in target_paths:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(serialized, encoding="utf-8")
            created.append(str(target.relative_to(data_root)))
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--artifact", type=Path, help="Offline local ZIP for testing")
    args = parser.parse_args()
    if args.artifact:
        raw = args.artifact.read_bytes()
    else:
        raw = download_attested_zip(os.getenv("GH_TOKEN", ""))
    candidate = verified_issue(raw)
    created = seed_only_if_absent(args.data_root, candidate)
    print(json.dumps({
        "run_id": RUN_ID, "artifact_id": ARTIFACT_ID,
        "issue_id": "jegrowth-31-3",
        "source_status": candidate["source_status"],
        "publication_state": candidate["publication_state"],
        "changed_paths": created,
        "production_enabled": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
