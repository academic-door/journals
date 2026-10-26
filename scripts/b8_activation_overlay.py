"""B8 one-title zero-downtime derived-index bridge for pending promotion.

The official data branch remains the canonical producer. An activation PR may
temporarily see a source-verified JEGROWTH issue already staged on data while
derived indexes still describe the old 49-title configuration. In a disposable
Actions checkout ONLY, rebuild those derived indexes against the approved new
50-title config and run the same strict source audits. Never write the data
branch here; the normal serialized Monitor remains the production publisher.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.seed_b8_jegrowth_release import EXPECTED_DOIS
from scripts.update_journals import (
    is_archivable_snapshot,
    issue_translation_semantics_valid,
    normalize_issue_content,
    read_json,
    validate_issue,
)

API = ROOT / "public/api/v1"
CONFIG = ROOT / "config/journals.yml"
B8 = ROOT / "config/journal-expansion-b8.json"


def review_activation_transition(
    manifest: dict, configs: dict, health: dict, journal_issue: dict | None
) -> str:
    growth = configs.get("JEGROWTH")
    enabled = [x for x in configs.values() if x.get("enabled")]
    if growth is None:
        if len(enabled) != 49:
            raise ValueError("unapproved journal count before B8 growth activation")
        return "no_op"
    if not growth.get("enabled") or len(enabled) != 50:
        raise ValueError("JEGROWTH must be the sole approved newly enabled journal")
    if growth.get("id") != "jegrowth" or growth.get("collector") != "springer":
        raise ValueError("JEGROWTH collector and identity not source-qualified")
    if growth.get("current_issue_url") != "https://link.springer.com/journal/10887/volumes-and-issues/31-3":
        raise ValueError("JEGROWTH current issue URL is not the approved official source")
    if not journal_issue:
        raise ValueError("production data lacks the source-verified JEGROWTH current issue")
    validate_issue(journal_issue)
    if normalize_issue_content(json.loads(json.dumps(journal_issue))) != journal_issue:
        raise ValueError("JEGROWTH issue payload is not normalized")
    if journal_issue.get("issue_id") != "jegrowth-31-3":
        raise ValueError("JEGROWTH issue identity drifted")
    if tuple(x.get("doi") for x in journal_issue["articles"]) != EXPECTED_DOIS:
        raise ValueError("JEGROWTH DOI/order differs from official issue")
    if journal_issue.get("source_status") != "official_verified" or journal_issue.get("publication_state") != "ready":
        raise ValueError("JEGROWTH official-source READY gate failed")
    if not is_archivable_snapshot(journal_issue) or not issue_translation_semantics_valid(journal_issue):
        raise ValueError("JEGROWTH title/abstract Chinese or numeric gate failed")

    old_count = int(health.get("summary", {}).get("enabled_journals", -1))
    if old_count == 50:
        if int(health.get("summary", {}).get("available_journals", -1)) != 50:
            raise ValueError("production-derived health already 50 but lacks availability")
        return "no_op"
    if old_count != 49 or int(health.get("summary", {}).get("available_journals", -1)) != 49:
        raise ValueError("unexpected prior production health baseline")
    approved = next((x for x in manifest.get("journals", []) if x.get("key") == "JEGROWTH"), {})
    if manifest.get("launch_state") != "partial_rollout_pending_verification" or approved.get("rollout_status") != "activation_pending_production":
        raise ValueError("transient derived-index rebuild needs exact B8 activation contract")
    return "rebuild"


def main() -> int:
    manifest = json.loads(B8.read_text(encoding="utf-8"))
    configs = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
    health = read_json(API / "health.json") or {}
    current = read_json(API / "journals/jegrowth/issues/current.json")
    state = review_activation_transition(manifest, configs, health, current)
    if state == "no_op":
        print("B8 activation index bridge: no mutation required")
        return 0
    monitoring = read_json(API / "monitoring.json") or {}
    if monitoring.get("summary", {}).get("configured_journals") != 49:
        raise ValueError("B8 pre-activation monitoring baseline is not 49")
    project = read_json(ROOT / "public/project-manifest.json") or {}
    if project.get("journal_count") != 49:
        raise ValueError("B8 pre-activation public manifest baseline is not 49")
    fields = yaml.safe_load((ROOT / "config/collections.yml").read_text())["collections"]["fields"]
    if fields.get("journals", []).count("JEGROWTH") != 1:
        raise ValueError("JEGROWTH must belong to Fields exactly once")
    subprocess.run([sys.executable, str(ROOT / "scripts/rebuild_public_snapshot.py")], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/audit_source_alignment.py"), "--strict"], cwd=ROOT, check=True)
    generated = read_json(API / "health.json") or {}
    if generated.get("summary", {}).get("enabled_journals") != 50 or generated.get("summary", {}).get("available_journals") != 50:
        raise ValueError("ephemeral derived rebuild did not validate 50/50")
    print("B8 activation index bridge: rebuilt/strictly audited 50/50 in ephemeral Actions checkout only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
