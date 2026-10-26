"""Validate approved B8 staging identities without expanding production journals."""
from __future__ import annotations

import argparse
import json
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


def validate_batch(manifest: dict, live_journals: dict) -> dict:
    errors: list[str] = []
    items = manifest.get("journals")
    if not isinstance(items, list):
        raise ValueError("B8 journals must be a list")
    if manifest.get("schema_version") != "1.0":
        errors.append("invalid schema")
    if manifest.get("launch_state") != "staging_only":
        errors.append("unexpected production activation")
    if manifest.get("baseline_live_journals") != len(live_journals):
        errors.append("live journal count drift")
    if manifest.get("max_additional_titles") != 8 or len(items) != 8:
        errors.append("batch must contain exactly eight approved targets")
    keys = [item.get("key") for item in items]
    ids = [item.get("id") for item in items]
    issns = [item.get("issn") for item in items]
    if set(keys) != EXPECTED or len(set(keys)) != len(keys):
        errors.append("batch titles drifted from approved eight")
    if len(set(ids)) != len(ids) or len(set(issns)) != len(issns):
        errors.append("duplicate journal id or ISSN")
    live_ids = {str(x["id"]) for x in live_journals.values()}
    live_issns = {str(x.get("issn") or "") for x in live_journals.values()}
    for item in items:
        family = item.get("publisher_family")
        key = str(item.get("key") or "")
        source = urlparse(str(item.get("source_url") or ""))
        if family not in FAMILIES or (
            source.scheme != "https" or source.hostname != FAMILIES.get(family)
        ):
            errors.append(f"{key}: invalid source family or host")
        if key in live_journals or item.get("id") in live_ids:
            errors.append(f"{key}: already live")
        if item.get("issn") in live_issns:
            errors.append(f"{key}: ISSN collision")
        if item.get("rollout_status") != "not_enabled":
            errors.append(f"{key}: unverified title enabled")
        if not str(item.get("name") or ""):
            errors.append(f"{key}: title missing")
        if not str(item.get("issn") or "") or len(str(item["issn"])) != 9:
            errors.append(f"{key}: invalid ISSN")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "existing_live": len(live_journals),
        "approved_staged": len(items),
        "by_publisher": dict(sorted(Counter(x["publisher_family"] for x in items).items())),
        "promoted": 0,
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
