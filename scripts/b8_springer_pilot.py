"""B8 single-title Springer pilot: official source -> isolated readiness candidate.

The approved B8 manifest remains staging-only. This command deliberately never
updates config/journals.yml, a public issue, or the shared data branch. A pilot
must be independently accepted before a separate guarded promotion.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_archives_from_roster_evidence import build_candidate_from_evidence
from scripts.capture_springer_roster_evidence import capture_record
from scripts.import_official_roster_evidence import _normalized_title, validate_evidence
from scripts.translate_issue import translate_missing
from scripts.update_journals import (
    apply_translation_cache,
    is_archivable_snapshot,
    issue_translation_semantics_valid,
    normalize_issue_content,
    stamp_issue_readiness,
    validate_issue,
)

FIXTURE = ROOT / "tests/fixtures/b8/springer-source-stage.json"
MANIFEST = ROOT / "config/journal-expansion-b8.json"


def approved_pilot(stage: dict, manifest: dict, key: str = "JEGROWTH") -> tuple[dict, dict]:
    """Fail closed unless the complete official stage roster matches approved scope."""
    if manifest.get("launch_state") != "staging_only":
        raise ValueError("B8 approval manifest must remain staging-only")
    approved = [x for x in manifest.get("journals", []) if x.get("key") == key]
    observed = [x for x in stage.get("journals", []) if x.get("journal") == key]
    if len(approved) != 1 or len(observed) != 1:
        raise ValueError("approved pilot identity or stage evidence missing")
    spec, source = approved[0], observed[0]
    if spec.get("rollout_status") != "not_enabled":
        raise ValueError("pilot must not be production-enabled")
    if spec.get("publisher_family") != "springer":
        raise ValueError("wrong publisher family")
    if not source.get("verified_full_displayed_toc"):
        raise ValueError("publisher complete issue not independently verified")
    if not source.get("source_authority") == "first_party_springer_full_issue":
        raise ValueError("official source authority missing")
    if not source.get("source_items") or len(source["source_items"]) != source.get("source_item_count"):
        raise ValueError("official source item count mismatch")
    official_url = str(source.get("official_url", ""))
    parsed = urlparse(official_url)
    if parsed.scheme != "https" or parsed.hostname != "link.springer.com":
        raise ValueError("untrusted official issue URL")
    if not parsed.path.startswith("/journal/10887/volumes-and-issues/"):
        raise ValueError("pilot uses wrong Springer journal")
    if source.get("issue_id") != "jegrowth-31-3" or spec.get("sample_issue") != "31/3":
        raise ValueError("unexpected pilot volume / issue")
    for i, row in enumerate(source["source_items"], start=1):
        if row.get("source_sequence") != i or row.get("publisher_type") != "OriginalPaper":
            raise ValueError("unverified official order or publisher research type")
        doi = str(row.get("doi", "")).lower()
        if not doi.startswith("10.1007/s10887-"):
            raise ValueError("DOI outside Springer growth title")
        if row.get("source_url") != f"https://link.springer.com/article/{doi}":
            raise ValueError("untrusted article URL")
    if len({row["doi"].lower() for row in source["source_items"]}) != len(source["source_items"]):
        raise ValueError("duplicate official DOI")
    return spec, source


def verify_capture(evidence: dict, stage: dict) -> None:
    """Live publisher HTML and detail metadata must equal the immutable stage."""
    validate_evidence(evidence)
    if evidence.get("journal_id") != "jegrowth" or evidence.get("issue_id") != stage["issue_id"]:
        raise ValueError("captured journal or issue identity changed")
    if evidence.get("official_url") != stage["official_url"]:
        raise ValueError("captured from a different publisher issue")
    if evidence.get("excluded_items") or evidence.get("excluded_item_count") != 0:
        raise ValueError("publisher types changed since full-issue observation")
    expected = stage["source_items"]
    observed = evidence["items"]
    if len(observed) != len(expected):
        raise ValueError("publisher issue length changed")
    for i, (captured, pinned) in enumerate(zip(observed, expected, strict=True), start=1):
        if captured["sequence"] != i or captured["doi"] != pinned["doi"].lower():
            raise ValueError(f"publisher DOI sequence changed at {i}")
        if _normalized_title(captured["title_en"]) != _normalized_title(pinned["title_en"]):
            raise ValueError(f"publisher title changed at {i}")
        if captured["source_url"] != pinned["source_url"]:
            raise ValueError(f"publisher article URL changed at {i}")
        if not captured["authors"] or len(str(captured["abstract_en"]).strip()) < 80:
            raise ValueError(f"missing or unexpectedly short official abstract at {i}")


def make_candidate(evidence: dict, spec: dict, stage: dict) -> dict:
    candidate = build_candidate_from_evidence(
        evidence,
        {"name": spec["name"], "issn": spec["issn"]},
        {},
        publication_date="September 2026",
    )
    candidate = normalize_issue_content(candidate)
    stamp_issue_readiness(candidate)
    validate_issue(candidate)
    if [x["doi"] for x in candidate["articles"]] != [
        x["doi"].lower() for x in stage["source_items"]
    ]:
        raise ValueError("candidate lost official DOI order")
    if candidate["source_status"] != "official_verified":
        raise ValueError("candidate official provenance not accepted")
    if candidate["research_article_count"] != len(stage["source_items"]):
        raise ValueError("candidate dropped publishable publisher article")
    return candidate


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    if args.require_ready and not args.translate:
        parser.error("--require-ready requires --translate")
    spec, stage = approved_pilot(
        json.loads(args.fixture.read_text(encoding="utf-8")),
        json.loads(args.manifest.read_text(encoding="utf-8")),
    )
    volume, number = spec["sample_issue"].split("/")
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    evidence = capture_record(
        {
            "journal": "JEGROWTH",
            "issue_id": stage["issue_id"],
            "volume": volume,
            "issue": number,
            "official_url": stage["official_url"],
            "springer_journal_code": "10887",
            "issn": spec["issn"],
        },
        captured_at=observed_at,
        detail_workers=3,
    )
    verify_capture(evidence, stage)
    issue = make_candidate(evidence, spec, stage)
    write_json(args.output_dir / "jegrowth-31-3.official-roster.json", evidence)
    if args.translate:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory(prefix="b8-growth-translation-") as tmp:
            cache_path = Path(tmp) / "jegrowth.json"
            translate_missing(issue, cache_path, max_translations=3)
            issue = apply_translation_cache(issue, cache_path=cache_path)
        issue = normalize_issue_content(issue)
        stamp_issue_readiness(issue)
        validate_issue(issue)
    ready = (
        issue.get("publication_state") == "ready"
        and is_archivable_snapshot(issue)
        and issue_translation_semantics_valid(issue)
    )
    write_json(args.output_dir / "jegrowth-31-3.candidate.json", issue)
    result = {
        "issue_id": stage["issue_id"],
        "official_dois": [x["doi"] for x in evidence["items"]],
        "research_articles": len(issue["articles"]),
        "source_status": issue["source_status"],
        "content_status": issue["content_status"],
        "publication_state": issue["publication_state"],
        "translation_semantics_valid": issue_translation_semantics_valid(issue),
        "quality_ready": ready,
        "live_promoted": False,
        "scope": "isolated-b8-candidate-no-live-config-or-data-mutation",
    }
    write_json(args.output_dir / "report.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ready or not args.require_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
