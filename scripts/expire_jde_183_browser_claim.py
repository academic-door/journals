"""Conservatively expire JDE 183(C)'s stale *browser* order verification.

A 90-day human/browser confirmation is not renewed by changing a timestamp.
Retain it as historical evidence; source-grade falls back to the publisher's
attested RSS issue roster, with official page order marked pending. Preserve
every published article, translation, DOI, right, and 53-journal availability.
Only the existing serialized data writer may commit changes after acceptance.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path

ISSUE = "jde-183-c"
RSS_URL = "https://rss.sciencedirect.com/publication/science/03043878"
ISSUE_URL = "https://www.sciencedirect.com/journal/journal-of-development-economics/vol/183/suppl/C"
CURRENT = "public/api/v1/journals/jde/issues/current.json"
ARCHIVE = "public/api/v1/journals/jde/issues/jde-183-c.json"
DETECTED = "public/api/v1/journals/jde/issues/detected.json"
INDEX = "public/api/v1/journals/jde/issues/index.json"
FIELDS = "public/api/v1/collections/fields.json"
ALLOWED = (CURRENT, ARCHIVE, DETECTED, INDEX, FIELDS)
OLD_AUTHORITY = "official-issue-page"
OLD_TRANSPORT = "official-issue-page+browser-authorized"


def read(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"missing data path: {path}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("unexpected data root type")
    return obj


def validate_ledger(ledger: dict, now: datetime) -> dict:
    entry = ledger.get("entries", {}).get("jde", {})
    if (
        entry.get("journal_id") != "jde"
        or entry.get("issue_id") != ISSUE
        or entry.get("official_url") != ISSUE_URL
        or entry.get("method") != "browser-authorized"
        or entry.get("item_count") != 36
        or not entry.get("sequence_sha256")
    ):
        raise ValueError("JDE source ledger identity is not the accepted 183(C) record")
    expiry = datetime.fromisoformat(str(entry.get("expires_at", "")).replace("Z", "+00:00"))
    if expiry.tzinfo is None:
        raise ValueError("JDE provenance ledger expiry lacks a timezone")
    if now <= expiry:
        raise ValueError("browser claim has not expired; no downgrade authorized")
    return entry


def validate_issue(issue: dict, ledger_entry: dict, allow_corrected: bool) -> bool:
    q = issue.get("quality", {})
    articles = issue.get("articles", [])
    if (
        issue.get("issue_id") != ISSUE
        or issue.get("journal_id") != "jde"
        or issue.get("volume") != "183"
        or issue.get("publication_date") != "September 2026"
        or issue.get("source_url") != ISSUE_URL
        or issue.get("research_article_count") != 34
        or issue.get("expected_article_count") != 34
        or len(articles) != 34
        or issue.get("content_status") != "complete"
        or issue.get("publication_state") != "ready"
        or q.get("rss_url") != RSS_URL
        or q.get("roster_match_scope") != "rss-volume-items"
        or not q.get("roster_match")
        or not q.get("order_preserved")
        or q.get("official_item_count") != 36
        or q.get("publishable_item_count") != 34
        or q.get("excluded_item_count") != 2
        or len(q.get("excluded_items", [])) != 2
        or q.get("doi_complete") != 34
        or q.get("authors_complete") != 34
        or q.get("abstract_en_complete") != 34
        or q.get("translation_complete") != 34
        or any(
            a.get("sequence") != i
            or a.get("article_type") != "research-article"
            or not str(a.get("doi", "")).startswith("10.1016/j.jdeveco.")
            for i, a in enumerate(articles, 1)
        )
    ):
        raise ValueError("JDE 183 publisher-RSS source/content identity drift")
    historical = q.get("historical_browser_order_verification")
    corrected = historical is not None
    marker = historical if corrected else q.get("browser_order_verification")
    if not isinstance(marker, dict) or not marker.get("pii_sequence_matched"):
        raise ValueError("JDE historical browser claim not present")
    if marker.get("captured_at") != ledger_entry.get("captured_at"):
        raise ValueError("ledger and published browser attestation diverge")
    if corrected:
        if not allow_corrected or (
            issue.get("source_status") != "publisher_verified"
            or q.get("roster_authority") != "publisher-rss"
            or q.get("roster_transport") != "sciencedirect-rss"
            or q.get("order_verification") != "pending_official"
            or "official_order_unverified" not in q.get("flags", [])
            or "browser_order_verification" in q
            or "browser_capture" in q
            or not isinstance(q.get("historical_browser_capture"), dict)
        ):
            raise ValueError("JDE previously-corrected provenance is inconsistent")
    elif (
        issue.get("source_status") != "official_verified"
        or q.get("roster_authority") != OLD_AUTHORITY
        or q.get("roster_transport") != OLD_TRANSPORT
        or q.get("order_verification") != "official_verified"
        or not isinstance(q.get("browser_capture"), dict)
        or "historical_browser_capture" in q
    ):
        raise ValueError("JDE official-browser source claim changed unexpectedly")
    return corrected


def correct_quality(issue: dict) -> dict:
    result = copy.deepcopy(issue)
    q = result["quality"]
    q["historical_browser_order_verification"] = q.pop("browser_order_verification")
    q["historical_browser_capture"] = q.pop("browser_capture")
    q["roster_authority"] = "publisher-rss"
    q["roster_transport"] = "sciencedirect-rss"
    q["order_verification"] = "pending_official"
    q["flags"] = list(dict.fromkeys([*q.get("flags", []), "official_order_unverified"]))
    result["source_status"] = "publisher_verified"
    return result


def correct(data_root: Path, ledger: dict, *, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    entry = validate_ledger(ledger, now)
    paths = {name: data_root / name for name in ALLOWED}
    docs = {name: read(path) for name, path in paths.items()}
    current, archive, detected = (docs[name] for name in (CURRENT, ARCHIVE, DETECTED))
    if current != archive:
        raise ValueError("JDE current and immutable issue snapshots diverged")
    if [x.get("doi") for x in current["articles"]] != [x.get("doi") for x in detected.get("articles", [])]:
        raise ValueError("JDE detected issue DOI order differs from ready current")
    states = [validate_issue(obj, entry, True) for obj in (current, archive, detected)]
    if len(set(states)) != 1:
        raise ValueError("JDE publication layers have mixed source grade")
    fixed = states[0]
    field = next((x for x in docs[FIELDS].get("journals", []) if x.get("journal_id") == "jde"), None)
    indexed = next((x for x in docs[INDEX].get("issues", []) if x.get("issue_id") == ISSUE), None)
    if not isinstance(field, dict) or not isinstance(indexed, dict):
        raise ValueError("JDE public index entries missing")
    old = "publisher_verified" if fixed else "official_verified"
    old_auth = "publisher-rss" if fixed else OLD_AUTHORITY
    old_transport = "sciencedirect-rss" if fixed else OLD_TRANSPORT
    old_order = "pending_official" if fixed else "official_verified"
    exact = {
        "latest_detected_issue_id": ISSUE,
        "latest_issue_id": ISSUE,
        "latest_ready_issue_id": ISSUE,
        "status": "ready",
        "source_status": old,
        "latest_detected_source_status": old,
        "roster_authority": old_auth,
        "latest_detected_roster_authority": old_auth,
        "roster_transport": old_transport,
        "latest_detected_roster_transport": old_transport,
        "order_verification": old_order,
        "latest_detected_order_verification": old_order,
    }
    if any(field.get(k) != v for k, v in exact.items()):
        raise ValueError("JDE Fields references drifted; refusing partial source correction")
    if indexed.get("source_status") != old or indexed.get("publication_state") != "ready":
        raise ValueError("JDE archive index source grade drifted")
    if fixed:
        return []
    for name in (CURRENT, ARCHIVE, DETECTED):
        docs[name] = correct_quality(docs[name])
        validate_issue(docs[name], entry, True)
    for key in ("source_status", "latest_detected_source_status"):
        field[key] = "publisher_verified"
    for key in ("roster_authority", "latest_detected_roster_authority"):
        field[key] = "publisher-rss"
    for key in ("roster_transport", "latest_detected_roster_transport"):
        field[key] = "sciencedirect-rss"
    for key in ("order_verification", "latest_detected_order_verification"):
        field[key] = "pending_official"
    indexed["source_status"] = "publisher_verified"
    for name in ALLOWED:
        paths[name].write_text(json.dumps(docs[name], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return list(ALLOWED)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    changed = correct(args.data_root, read(args.ledger))
    print(json.dumps({
        "issue": ISSUE, "publisher_rss_source_remains": True,
        "expired_browser_claim_preserved_historically": True,
        "order_recheck_pending": True,
        "publisher_source_grade": "publisher_verified",
        "article_modifications": 0,
        "rights_or_translations_modified": False,
        "changed_paths": changed,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
