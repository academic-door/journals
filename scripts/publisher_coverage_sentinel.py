"""Read-only, fail-closed publisher DOI/type/order comparator.

Publisher coverage is separate from archival translation READY. No network access,
production mutations, scheduler, or API changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
PII_RE = re.compile(r"(?:^pii:|/pii/)([A-Z0-9]+)(?:[?/#]|$)", re.I)
ISSUE_URL = re.compile(r"^/journal/[^/]+/vol/([^/]+)/suppl/([^/]+)/?$", re.I)
METHODS = {"browser-authorized", "official-page-read", "official-issue-page"}
PILOT_SLUGS = {
    "jeem": "journal-of-environmental-economics-and-management",
    "jie": "journal-of-international-economics",
    "energy": "energy-economics",
}


def doi(value):
    value = str(value or "").strip().lower()
    return value if DOI_RE.fullmatch(value) else ""


def pii(row):
    values = []
    for field in ("source_id", "official_article_url", "source_url", "href"):
        match = PII_RE.search(str(row.get(field) or ""))
        if match:
            values.append(match.group(1).upper())
    return values[0] if values and len(set(values)) == 1 else ""


def valid_time(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def reconcile(publisher, archive, *, last_verified_at=None):
    """Compute diagnostic differences; only fully identified official evidence verifies."""
    errors = []
    journal = str(archive.get("journal_id") or "").lower()
    issue = str(archive.get("issue_id") or "").lower()
    if not journal or not issue:
        errors.append("archive_identity_missing")
    if (str(publisher.get("journal_id") or "").lower(),
            str(publisher.get("issue_id") or "").lower()) != (journal, issue):
        errors.append("publisher_archive_identity_mismatch")
    official_url = str(publisher.get("official_url") or "")
    parsed = urlparse(official_url)
    match = ISSUE_URL.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "www.sciencedirect.com" or not match:
        errors.append("invalid_official_issue_url")
    elif not issue.endswith("-" + match.group(1).lower() + "-" + match.group(2).lower()):
        errors.append("official_url_volume_issue_mismatch")
    if journal not in PILOT_SLUGS:
        errors.append("journal_outside_approved_pilot")
    elif parsed.path.split("/")[2:3] != [PILOT_SLUGS[journal]]:
        errors.append("official_url_journal_mismatch")
    method = str(publisher.get("method") or "")
    if method not in METHODS:
        errors.append("source_method_not_authorized")
    observed_at = publisher.get("captured_at") or publisher.get("observed_at")
    if not valid_time(observed_at):
        errors.append("publisher_observed_at_missing_or_invalid")
    if publisher.get("source_error"):
        errors.append("publisher_fetch_failed")
    if publisher.get("finalized") is False:
        errors.append("publisher_observation_not_final")

    pub_items = publisher.get("items")
    pub_excluded = publisher.get("excluded_items")
    computable = isinstance(pub_items, list) and isinstance(pub_excluded, list)
    if not computable:
        errors.append("publisher_rows_not_available")
        pub_items, pub_excluded = [], []
    elif not pub_items and not pub_excluded:
        errors.append("publisher_empty_roster_unverifiable")
    archive_items = archive.get("articles")
    archive_excluded = (archive.get("quality") or {}).get("excluded_items")
    if not isinstance(archive_items, list) or not isinstance(archive_excluded, list):
        errors.append("archive_rows_invalid")
        archive_items, archive_excluded = [], []

    seen_doi, seen_pii, seen_sequence = set(), set(), set()
    research, excluded, types, official_pii_by_doi = [], [], {}, {}
    for category, rows in (("research", pub_items), ("excluded", pub_excluded)):
        ordinals = []
        for row in rows:
            if not isinstance(row, dict):
                errors.append("publisher_row_not_object")
                continue
            d, p = doi(row.get("doi")), pii(row)
            if not d:
                errors.append("publisher_doi_missing_or_invalid")
            elif d in seen_doi:
                errors.append("publisher_duplicate_doi")
            else:
                seen_doi.add(d)
                (research if category == "research" else excluded).append(d)
                types[d] = str(row.get("publisher_article_type") or row.get("article_type") or row.get("reason") or category)
            if not p:
                errors.append("publisher_pii_missing_or_conflicting")
            elif p in seen_pii:
                errors.append("publisher_duplicate_pii")
            else:
                seen_pii.add(p)
                if d:
                    official_pii_by_doi[d] = p
            seq = row.get("source_sequence")
            if not isinstance(seq, int) or seq <= 0:
                errors.append("publisher_ordinal_missing")
            else:
                ordinals.append(seq)
                if seq in seen_sequence:
                    errors.append("publisher_duplicate_ordinal")
                seen_sequence.add(seq)
            if not str(row.get("title_en") or row.get("title") or "").strip():
                errors.append("publisher_title_missing")
            if category == "excluded" and not (row.get("reason") or row.get("article_type")):
                errors.append("publisher_exclusion_type_missing")
        if ordinals != sorted(ordinals):
            errors.append("publisher_order_not_preserved")
    if isinstance(publisher.get("excluded_item_count"), int) and publisher["excluded_item_count"] != len(pub_excluded):
        errors.append("publisher_exclusion_count_conflict")

    def archive_dois(rows, kind):
        values = []
        for row in rows:
            d = doi(row.get("doi")) if isinstance(row, dict) else ""
            if not d:
                errors.append("archive_" + kind + "_doi_missing_or_invalid")
            else:
                if d in values:
                    errors.append("archive_duplicate_" + kind + "_doi")
                values.append(d)
        return values

    archived = archive_dois(archive_items, "research")
    archived_ex = archive_dois(archive_excluded, "excluded")
    if set(archived) & set(archived_ex):
        errors.append("archive_type_identity_collision")
    archive_pii_seen = set()
    for row in archive_items + archive_excluded:
        if not isinstance(row, dict):
            continue
        identifier = pii(row)
        if not identifier:
            continue  # Archive may legitimately lack first-party PII.
        if identifier in archive_pii_seen:
            errors.append("archive_duplicate_pii")
        archive_pii_seen.add(identifier)
        official_identifier = official_pii_by_doi.get(doi(row.get("doi")))
        if official_identifier and identifier != official_identifier:
            errors.append("publisher_archive_pii_conflict")

    pub_set, archive_set = set(research), set(archived)
    excluded_set, archived_ex_set = set(excluded), set(archived_ex)
    shared = pub_set & archive_set
    wrong_order = [d for d in research if d in shared] != [d for d in archived if d in shared]
    # Archived article_type is usually intentionally broad; only compare the
    # publisher subtype if the archive explicitly recorded a publisher subtype.
    declared = {doi(row.get("doi")): str(row.get("publisher_article_type")).lower()
                for row in archive_items if isinstance(row, dict) and row.get("publisher_article_type")}
    subtype_delta = sorted(d for d in shared if d in declared and declared[d] != types[d].lower())
    diff = {
        "missing_research_dois": sorted(pub_set - archive_set),
        "extra_archived_research_dois": sorted(archive_set - pub_set),
        "missing_exclusion_dois": sorted(excluded_set - archived_ex_set),
        "extra_archived_exclusion_dois": sorted(archived_ex_set - excluded_set),
        "research_misclassified_as_excluded": sorted(pub_set & archived_ex_set),
        "nonresearch_misclassified_as_research": sorted(excluded_set & archive_set),
        "publisher_subtype_mismatch_dois": subtype_delta,
        "research_order_mismatch": wrong_order,
        "publisher_research_order": research,
        "archive_research_order": archived,
        "publisher_article_types": types,
    }
    fingerprint = hashlib.sha256(json.dumps({
        "research": [(d, types[d]) for d in research],
        "excluded": [(d, types[d]) for d in excluded]
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if computable else None
    changed = (any(diff[key] for key in (
        "missing_research_dois", "extra_archived_research_dois",
        "missing_exclusion_dois", "extra_archived_exclusion_dois",
        "research_misclassified_as_excluded", "nonresearch_misclassified_as_research",
        "publisher_subtype_mismatch_dois")) or wrong_order)
    errors = sorted(set(errors))
    status = "verification_blocked" if errors else ("publisher_changed" if changed else "reconciled_as_of")
    return {
        "schema_version": "1.0", "journal_id": journal, "issue_id": issue,
        "publisher_coverage": status, "diagnostic_only": bool(errors),
        "observed_at": None if errors else observed_at,
        "last_verified_at": last_verified_at if errors else observed_at,
        "source_url": official_url, "source_method": method,
        "source_evidence_ref": publisher.get("evidence_ref"),
        "publisher_fingerprint_sha256": fingerprint,
        "publisher_research_count": len(research) if computable else None,
        "archive_research_count": len(archived),
        "publisher_excluded_count": len(excluded) if computable else None,
        "archive_excluded_count": len(archived_ex),
        "verification_errors": errors,
        "diff": diff if computable else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publisher", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--last-verified-at")
    args = parser.parse_args()
    pub = json.loads(args.publisher.read_text(encoding="utf-8"))
    arc = json.loads(args.archive.read_text(encoding="utf-8"))
    result = reconcile(pub, arc, last_verified_at=args.last_verified_at)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return {"reconciled_as_of": 0, "publisher_changed": 1, "verification_blocked": 2}[result["publisher_coverage"]]


if __name__ == "__main__":
    raise SystemExit(main())
