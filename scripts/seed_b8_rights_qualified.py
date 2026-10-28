"""One-title B8 source-attested metadata-only seed into a caller-owned data worktree.

No network, credentials or Git writes here. The trusted Actions workflow pins
the immutable successful source run/artifact and owns the serialized data push.
Private EN/CN staging records are NEVER copied to the public API payload.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.b8_rights_qualified import (
    MODE, project_b8_issue, validate_public_projection,
)
from scripts.update_journals import (
    is_archivable_snapshot, is_publishable_snapshot,
    issue_translation_semantics_valid, normalize_issue_content,
    validate_issue,
)

# Pinned to already-accepted, successful, private G20 B8 staging artifacts.
# Expiring artifact retrieval is never a reason to fabricate source data.
SPECS = {
    "JAE": ("jae", "jae-41-6", 12, "official_verified",
            40661974740, "b8-jae-41-6-staged-candidate",
            "jae-41-6.candidate.json", "report.json", 0),
    "JOEG": ("joeg", "joeg-26-5", 9, "official_verified",
             40667092535, "b8-joeg-26-5-staged-candidate",
             "joeg-26-5.candidate.json", "joeg-26-5.report.json", 1),
    "ITPF": ("itpf", "itpf-33-5", 11, "publisher_verified",
             40663275078, "b8-itpf-33-5-staged-candidate",
             "itpf-33-5.candidate.json", "itpf-33-5.report.json", 0),
    "IJIO": ("ijio", "ijio-108-C", 6, "publisher_verified",
             40668765777, "b8-elsevier-IJIO-candidate",
             "candidate.json", "report.json", 0),
    "JFI": ("jfi", "jfi-67-C", 7, "publisher_verified",
            40668765777, "b8-elsevier-JFI-candidate",
            "candidate.json", "report.json", 0),
    "EJPE": ("ejpe", "ejpe-94-C", 16, "publisher_verified",
             40668765777, "b8-elsevier-EJPE-candidate",
             "candidate.json", "report.json", 1),
    "JIMF": ("jimf", "jimf-168-C", 6, "publisher_verified",
             40668765777, "b8-elsevier-JIMF-candidate",
             "candidate.json", "report.json", 0),
}


def build_attested_projection(journal: str, candidate: dict, report: dict) -> dict:
    """Fail closed unless all source/translation acceptance remains intact."""
    if journal not in SPECS:
        raise ValueError("not an approved remaining B8 journal")
    journal_id, issue_id, count, source, *_rest, excluded_count = SPECS[journal]
    if (
        candidate.get("journal_id") != journal_id
        or str(candidate.get("issue_id", "")).casefold() != issue_id.casefold()
        or len(candidate.get("articles", [])) != count
        or candidate.get("research_article_count") != count
        or candidate.get("source_status") != source
        or candidate.get("content_status") != "complete"
        or candidate.get("publication_state") != "ready"
        or not candidate.get("quality", {}).get("roster_match")
        or not candidate.get("quality", {}).get("order_preserved")
        or len(candidate.get("quality", {}).get("excluded_items", [])) != excluded_count
    ):
        raise ValueError("attested B8 source/issue/DOI/type/count gate failed")
    if report.get("production_writes") is not False:
        raise ValueError("staging report is not isolated/private")
    if report.get("release_enabled", report.get("production_release_approved")) is not False:
        raise ValueError("source artifact unexpectedly claimed public activation")
    if report.get("content_quality_ready", report.get("quality_ready")) is not True:
        raise ValueError("source artifact report failed bilingual quality gate")
    validate_issue(candidate)
    if not is_archivable_snapshot(candidate) or not issue_translation_semantics_valid(candidate):
        raise ValueError("source candidate is not privately bilingual READY")
    if any(article.get("quality_flags") for article in candidate["articles"]):
        raise ValueError("candidate carries unaccepted article quality flags")

    # Empty rights grants are intentional: do not infer license from the
    # presence of a publisher-deposited English abstract or CN translation.
    public = project_b8_issue(
        candidate, expected_journal_id=journal_id,
        expected_issue_id=issue_id, rights_by_doi={},
    )
    public = normalize_issue_content(public)
    validate_issue(public)
    validate_public_projection(public)
    if not is_publishable_snapshot(public):
        raise ValueError("projected rights-qualified public issue failed READY")
    if (
        public.get("public_mode") != MODE
        or public["public_content"]["rights_withheld_articles"] != count
        or public["public_content"]["abstracts_en_visible"] != 0
        or public["public_content"]["translations_cn_visible"] != 0
        or any(
            article.get("title_cn") or article.get("abstract_en") or article.get("abstract_cn")
            for article in public["articles"]
        )
    ):
        raise ValueError("public metadata projection leaked private candidate content")
    return public


def seed_only_if_absent(data_root: Path, public: dict) -> list[str]:
    """Write exactly current and immutable issue paths, never override data."""
    journal_id, issue_id = public["journal_id"], public["issue_id"]
    if journal_id not in {spec[0] for spec in SPECS.values()}:
        raise ValueError("unknown B8 data seed identity")
    relative = Path("public/api/v1/journals") / journal_id / "issues"
    targets = [data_root / relative / "current.json",
               data_root / relative / f"{issue_id}.json"]
    serialized = json.dumps(public, ensure_ascii=False, indent=2) + "\n"
    for target in targets:
        if target.exists() and target.read_text(encoding="utf-8") != serialized:
            raise ValueError("existing public issue differs: " + target.name)
    changed = []
    for target in targets:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(serialized, encoding="utf-8")
            changed.append(str(target.relative_to(data_root)))
    return changed


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--journal", choices=sorted(SPECS), required=True)
    p.add_argument("--artifact-dir", type=Path, required=True)
    p.add_argument("--data-root", type=Path, required=True)
    args = p.parse_args()
    _, _, _, _, run_id, name, candidate_name, report_name, _ = SPECS[args.journal]
    candidate_path = args.artifact_dir / candidate_name
    report_path = args.artifact_dir / report_name
    if not candidate_path.is_file() or not report_path.is_file():
        raise ValueError("pinned private artifact files missing")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    projected = build_attested_projection(args.journal, candidate, report)
    changed = seed_only_if_absent(args.data_root, projected)
    print(json.dumps({
        "journal": args.journal, "attested_run_id": run_id,
        "artifact_name": name, "source_status": projected["source_status"],
        "content_status": projected["content_status"],
        "rights_withheld": projected["public_content"]["rights_withheld_articles"],
        "changed_paths": changed, "production_enabled": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
