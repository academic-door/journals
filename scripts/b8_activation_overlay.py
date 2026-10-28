"""B8 per-title, one-at-a-time derived-index bridge for pending promotions.

The data branch remains the sole production producer. In a disposable Actions
checkout, rebuild stale derived indexes only after a newly enabled title has
an already staged, source-verified, bilingual READY current issue. Never push
from here or claim a scheduled Monitor check that did not occur.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed_b8_jegrowth_release import EXPECTED_DOIS as GROWTH_DOIS
from scripts.update_journals import (
    is_archivable_snapshot,
    issue_translation_semantics_valid,
    normalize_issue_content,
    read_json,
    validate_issue,
)
from scripts.validate_journal_expansion_b8 import validate_batch

API = ROOT / "public/api/v1"
CONFIG = ROOT / "config/journals.yml"
B8 = ROOT / "config/journal-expansion-b8.json"
BASELINE = 49


def pending_promotion(manifest: dict) -> dict | None:
    pending = [
        item for item in manifest.get("journals", [])
        if item.get("rollout_status") == "activation_pending_production"
    ]
    if len(pending) > 1:
        raise ValueError("only one B8 title may be in a pending production transition")
    return pending[0] if pending else None


def review_activation_transition(
    manifest: dict, configs: dict, health: dict, journal_issue: dict | None,
) -> str:
    """Return no_op or rebuild; never infer READY from a config/metadata claim."""
    if "JEGROWTH" not in configs:
        if len([x for x in configs.values() if x.get("enabled")]) != BASELINE:
            raise ValueError("unapproved journal count before B8 activation")
        return "no_op"  # Historical pre-B8 fixture of the existing 49 titles.

    growth = configs["JEGROWTH"]
    if growth.get("collector") != "springer" or growth.get("id") != "jegrowth":
        raise ValueError("JEGROWTH collector and identity not source-qualified")

    # The separate validator prevents unapproved new names, source aliases,
    # unrelated publisher collectors, and missing per-title acceptance.
    validate_batch(manifest, configs)
    promoted = [
        item for item in manifest["journals"]
        if item.get("rollout_status") in {
            "activation_pending_production", "production_verified",
        }
    ]
    expected_count = BASELINE + len(promoted)
    observed = health.get("summary", {})
    old_count = int(observed.get("enabled_journals", -1))
    available_count = int(observed.get("available_journals", -1))
    pending = pending_promotion(manifest)

    if old_count == expected_count:
        if available_count != expected_count:
            raise ValueError("derived health has missing journal availability")
        return "no_op"

    # If a current issue is absent, report the content gate before giving a
    # confusing count mismatch (and preserve existing negative regression).
    if not journal_issue:
        raise ValueError("production data lacks the source-verified current issue")
    if old_count != expected_count - 1 or available_count != old_count:
        raise ValueError("unexpected production health baseline for B8 transition")
    if not pending or manifest.get("launch_state") != "partial_rollout_pending_verification":
        raise ValueError("no approved single-title transition to rebuild")

    key, journal_id = pending["key"], pending["id"]
    expected_issue_id = f"{journal_id}-" + str(pending["sample_issue"]).replace("/", "-")
    validate_issue(journal_issue)
    if normalize_issue_content(copy.deepcopy(journal_issue)) != journal_issue:
        raise ValueError(f"{key}: source READY current payload is not normalized")
    if (
        journal_issue.get("journal_id") != journal_id
        or journal_issue.get("issue_id") != expected_issue_id
        or journal_issue.get("source_status") not in {
            "official_verified", "publisher_verified",
        }
        or journal_issue.get("publication_state") != "ready"
        or not journal_issue.get("quality", {}).get("roster_match")
        or not journal_issue.get("quality", {}).get("order_preserved")
    ):
        raise ValueError(f"{key}: official issue identity/order/READY gate failed")
    if key == "JEGROWTH" and tuple(
        article.get("doi") for article in journal_issue["articles"]
    ) != GROWTH_DOIS:
        raise ValueError("JEGROWTH DOI/order differs from accepted official issue")
    if not is_archivable_snapshot(journal_issue) or not issue_translation_semantics_valid(journal_issue):
        raise ValueError(f"{key}: bilingual/semantic publication gate failed")
    return "rebuild"


def main() -> int:
    manifest = json.loads(B8.read_text(encoding="utf-8"))
    configs = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["journals"]
    health = read_json(API / "health.json") or {}
    pending = pending_promotion(manifest)
    current = (
        read_json(API / f"journals/{pending['id']}/issues/current.json")
        if pending else None
    )
    state = review_activation_transition(manifest, configs, health, current)
    if state == "no_op":
        print("B8 activation index bridge: no mutation required")
        return 0

    assert pending is not None  # Checked in review_activation_transition.
    key = pending["key"]
    old_count = int(health["summary"]["enabled_journals"])
    new_count = old_count + 1
    monitoring = read_json(API / "monitoring.json") or {}
    observed_monitor_count = monitoring.get("summary", {}).get("configured_journals")
    if observed_monitor_count not in (old_count, new_count):
        raise ValueError("B8 monitoring has neither previous nor newly configured count")
    project = read_json(ROOT / "public/project-manifest.json") or {}
    if int(project.get("journal_count", -1)) != old_count:
        raise ValueError("B8 public manifest does not match pre-activation baseline")
    fields = yaml.safe_load((ROOT / "config/collections.yml").read_text(encoding="utf-8"))[
        "collections"
    ]["fields"]
    if fields.get("journals", []).count(key) != 1:
        raise ValueError(f"{key}: title must belong to Fields exactly once")

    # Recompute only in checkout; never touch GitHub data/main from this script.
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/rebuild_public_snapshot.py")],
        cwd=ROOT, check=True,
    )
    if observed_monitor_count == old_count:
        # The first real scheduled monitoring check has not happened.
        if key in (monitoring.get("warning_journals") or []):
            raise ValueError(f"{key}: unexpected preexisting monitor warning")
        monitoring["summary"]["configured_journals"] = new_count
        monitoring["summary"]["warnings"] = int(
            monitoring["summary"].get("warnings", 0)
        ) + 1
        monitoring["warning_journals"] = [
            *(monitoring.get("warning_journals") or []), key,
        ]
        monitoring["status"] = "degraded"
        monitoring["b8_transition_note"] = (
            f"{key} bilingual source READY; first operational probe pending"
        )
        (API / "monitoring.json").write_text(
            json.dumps(monitoring, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        # Never manufacture a green operational monitor result.
        observed_keys = (
            set(monitoring.get("last_successful_checks", {}))
            | set(monitoring.get("warning_journals", []))
            | set(monitoring.get("failed_journals", []))
            | set(monitoring.get("awaiting_official_journals", []))
        )
        if key not in observed_keys:
            raise ValueError(f"{key}: new monitor count omits promoted title")

    subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_source_alignment.py"), "--strict"],
        cwd=ROOT, check=True,
    )
    generated = read_json(API / "health.json") or {}
    summary = generated.get("summary", {})
    if (
        summary.get("enabled_journals") != new_count
        or summary.get("available_journals") != new_count
    ):
        raise ValueError(f"B8 transient rebuild did not validate {new_count}/{new_count}")
    print(
        f"B8 derived index: rebuilt and strictly audited {new_count}/{new_count} "
        "in disposable Actions checkout only"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
