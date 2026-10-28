"""Validate approved B8 identities and guarded per-title production transition."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = {
    "elsevier": "shop.elsevier.com",
    "oup": "academic.oup.com",
    "wiley": "onlinelibrary.wiley.com",
    "springer": "link.springer.com",
}
EXPECTED = {"IJIO", "JFI", "EJPE", "JIMF", "JOEG", "JAE", "JEGROWTH", "ITPF"}
STATES = {"not_enabled", "activation_pending_production", "production_verified"}
GROWTH_EXACT_SOURCE = "https://link.springer.com/journal/10887/volumes-and-issues/31-3"
# Each newly admitted title must use the already-supported publisher collector.
# Crossref can supplement article fields but cannot become issue-roster authority.
COLLECTORS = {
    "elsevier": "elsevier", "oup": "oup",
    "wiley": "wiley", "springer": "springer",
}
OFFICIAL_ISSUE_HOSTS = {
    "elsevier": "www.sciencedirect.com",
    "oup": "academic.oup.com",
    "wiley": "onlinelibrary.wiley.com",
    "springer": "link.springer.com",
}
ACCEPTANCE_RUN = re.compile(r"academic-door/journals/actions/runs/[1-9][0-9]*\\Z")
STAGING_ONLY_SOURCE_STATES = {
    "official_issue_list_observed",
    "full_sectioned_issue_observed",
    "publisher_repec_series_observed_first_party_issue_unverified",
}


def _admission_errors(item: dict, live: dict) -> list[str]:
    """Validate the per-title release claim, not an obsolete first-title allowlist.

    The producer's READY/content/translation gates and protected CI still apply;
    a typed acceptance run is the durable owner decision/evidence pointer.
    """
    key = str(item["key"])
    family = str(item["publisher_family"])
    errors: list[str] = []
    if live.get("collector") != COLLECTORS[family]:
        errors.append(f"{key}: collector is not qualified for its publisher family")
    issue_url = urlparse(str(live.get("current_issue_url") or ""))
    if (
        issue_url.scheme != "https"
        or issue_url.hostname != OFFICIAL_ISSUE_HOSTS[family]
        or not issue_url.path.strip("/")
    ):
        errors.append(f"{key}: official publisher issue URL missing or mismatched")
    if not ACCEPTANCE_RUN.fullmatch(str(item.get("acceptance_evidence") or "")):
        errors.append(f"{key}: missing per-title protected acceptance run")
    if (
        not str(item.get("sample_issue") or "").strip()
        or not str(item.get("source_status") or "").strip()
        or item.get("source_status") in STAGING_ONLY_SOURCE_STATES
    ):
        errors.append(f"{key}: source remains unqualified staging evidence")
    if key == "JEGROWTH" and (
        live.get("current_issue_url") != GROWTH_EXACT_SOURCE
        or item.get("acceptance_evidence")
        != "academic-door/journals/actions/runs/40326883547"
    ):
        # Do not alter the already accepted production admission retrospectively.
        errors.append(f"{key}: original independently accepted source admission changed")
    return errors



def validate_batch(manifest: dict, live_journals: dict) -> dict:
    errors: list[str] = []
    items = manifest.get("journals")
    if not isinstance(items, list):
        raise ValueError("B8 journals must be a list")
    if manifest.get("schema_version") != "1.0":
        errors.append("invalid schema")
    if manifest.get("baseline_live_journals") != 49:
        errors.append("B8 approved baseline drift")
    if manifest.get("max_additional_titles") != 8 or len(items) != 8:
        errors.append("batch must contain exactly eight approved targets")
    keys = [item.get("key") for item in items]
    ids = [item.get("id") for item in items]
    issns = [item.get("issn") for item in items]
    if set(keys) != EXPECTED or len(set(keys)) != len(keys):
        errors.append("batch titles drifted from approved eight")
    if len(set(ids)) != len(ids) or len(set(issns)) != len(issns):
        errors.append("duplicate journal id or ISSN")
    promoted = [x for x in items if x.get("rollout_status") in {"activation_pending_production", "production_verified"}]
    pending = [x for x in promoted if x.get("rollout_status") == "activation_pending_production"]
    expected_state = (
        "staging_only" if not promoted else
        "partial_rollout_pending_verification" if pending else "partial_rollout"
    )
    if manifest.get("launch_state") != expected_state:
        errors.append("B8 manifest launch_state mismatches independently promoted titles")
    if len(live_journals) != 49 + len(promoted):
        errors.append("live journal count drift beyond independently approved B8 titles")

    live_ids = [str(x.get("id", "")) for x in live_journals.values()]
    live_issns = [str(x.get("issn", "")) for x in live_journals.values()]
    if len(live_ids) != len(set(live_ids)) or len(live_issns) != len(set(live_issns)):
        errors.append("live journal id or ISSN collision")

    for item in items:
        family = item.get("publisher_family")
        key = str(item.get("key") or "")
        source = urlparse(str(item.get("source_url") or ""))
        if family not in FAMILIES or source.scheme != "https" or source.hostname != FAMILIES.get(family):
            errors.append(f"{key}: invalid source family or host")
        state = item.get("rollout_status")
        if state not in STATES:
            errors.append(f"{key}: unrecognized rollout state")
        live = live_journals.get(key)
        if live is None:
            if state != "not_enabled":
                errors.append(f"{key}: promoted without live configuration")
            if item.get("id") in live_ids or item.get("issn") in live_issns:
                errors.append(f"{key}: identity collision")
        else:
            if state == "not_enabled":
                errors.append(f"{key}: enabled without independent promotion gate")
            if not live.get("enabled") or str(live.get("id")) != item.get("id") or str(live.get("issn")) != item.get("issn"):
                errors.append(f"{key}: live identity or enabled state mismatch")
            # Any approved B8 title can advance independently once it carries
            # its own publisher-aligned admission, without changing other titles.
            errors.extend(_admission_errors(item, live))
        if not str(item.get("name") or ""):
            errors.append(f"{key}: title missing")
        if not str(item.get("issn") or "") or len(str(item["issn"])) != 9:
            errors.append(f"{key}: invalid ISSN")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "existing_live": 49,
        "approved_staged": len(items),
        "by_publisher": dict(sorted(Counter(x["publisher_family"] for x in items).items())),
        "promoted": len(promoted),
        "production_verification_pending": len(pending),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "config/journal-expansion-b8.json")
    parser.add_argument("--live-config", type=Path, default=ROOT / "config/journals.yml")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    configs = yaml.safe_load(args.live_config.read_text(encoding="utf-8"))["journals"]
    print(json.dumps(validate_batch(manifest, configs), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
