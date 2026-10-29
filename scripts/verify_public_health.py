import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
now = datetime.now(timezone.utc)

def load(name):
    return json.loads((root / name).read_text(encoding="utf-8"))

def timestamp_field(payload, name, field, max_age_hours):
    raw = str(payload.get(field, "")).replace("Z", "+00:00")
    stamp = datetime.fromisoformat(raw)
    if stamp.tzinfo is None:
        raise AssertionError(f"{name}.{field} has no timezone")
    age = (now - stamp.astimezone(timezone.utc)).total_seconds() / 3600
    if age < -0.25 or age > max_age_hours:
        raise AssertionError(f"{name} is stale ({age:.1f} hours)")
    return stamp

def timestamp(payload, name, max_age_hours):
    return timestamp_field(payload, name, "updated_at", max_age_hours)

health = load("health.json")
monitoring = load("monitoring.json")
source = load("source-audit.json")
diagnostics = load("source-diagnostics.json")
slo = load("slo.json")
completeness = load("completeness-2026.json")
manifest = load("project-manifest.json")
for name, payload in (
    ("health", health),
    ("monitoring", monitoring),
    ("source-audit", source),
):
    if payload.get("schema_version") != "1.0":
        raise AssertionError(f"{name} schema_version is not 1.0")

summary = health.get("summary", {})
expected = summary.get("enabled_journals")
if expected not in (49, 50, 51, 52, 53):
    raise AssertionError("health enabled_journals is outside the approved 49/50/51/52/53 rollout")
if summary.get("available_journals") != expected:
    raise AssertionError("not every configured journal has a READY issue")
if expected >= 50:
    # First independently approved B8 title remains required.
    growth = load("jegrowth-current.json")
    expected_dois = [
        "10.1007/s10887-026-09265-x",
        "10.1007/s10887-026-09267-9",
        "10.1007/s10887-026-09266-w",
    ]
    articles = growth.get("articles", [])
    if growth.get("journal_id") != "jegrowth" or growth.get("issue_id") != "jegrowth-31-3":
        raise AssertionError("newly enabled journal identity is not approved JEGROWTH 31(3)")
    if [str(a.get("doi", "")) for a in articles] != expected_dois:
        raise AssertionError("JEGROWTH published DOI sequence differs from first-party source")
    if growth.get("source_status") != "official_verified" or growth.get("publication_state") != "ready":
        raise AssertionError("JEGROWTH is not independently source-qualified READY")
    quality = growth.get("quality", {})
    if quality.get("translation_complete") != 3 or quality.get("flags"):
        raise AssertionError("JEGROWTH translation/quality gate failed")
    if any(
        not a.get("authors") or not a.get("title_en") or not a.get("title_cn")
        or not a.get("abstract_en") or not a.get("abstract_cn")
        for a in articles
    ):
        raise AssertionError("JEGROWTH public author/English/Chinese fields are incomplete")
if expected >= 51:
    jae = load("jae-current.json")
    jae_articles = jae.get("articles", [])
    expected_jae_dois = [
        "10.1002/jae.70067", "10.1002/jae.70061",
        "10.1002/jae.70071", "10.1002/jae.70074",
        "10.1002/jae.70076", "10.1002/jae.70078",
        "10.1002/jae.70077", "10.1002/jae.70073",
        "10.1002/jae.70075", "10.1002/jae.70069",
        "10.1002/jae.70064", "10.1002/jae.70072",
    ]
    if (
        jae.get("journal_id") != "jae"
        or jae.get("issue_id") != "jae-41-6"
        or jae.get("source_url") != "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6"
        or jae.get("source_status") != "official_verified"
        or jae.get("content_status") != "metadata_complete"
        or jae.get("publication_state") != "ready"
        or jae.get("public_mode") != "metadata_first_rights_qualified_v1"
        or [str(a.get("doi", "")) for a in jae_articles] != expected_jae_dois
    ):
        raise AssertionError("JAE 41(6) metadata/DOI/source authority is not accepted")
    quality = jae.get("quality", {})
    rights = jae.get("public_content", {})
    if (
        quality.get("roster_match") is not True
        or quality.get("order_preserved") is not True
        or quality.get("excluded_items", [])
        or rights.get("metadata_articles") != 12
        or rights.get("rights_withheld_articles") != 12
        or rights.get("abstracts_en_visible") != 0
        or rights.get("translations_cn_visible") != 0
    ):
        raise AssertionError("JAE ordered issue count/rights projection differs from acceptance")
    if any(
        a.get("article_type") != "research-article"
        or a.get("sequence") != i
        or not a.get("authors") or not a.get("title_en")
        or any(str(a.get(k, "")).strip() for k in
               ("abstract_en", "abstract_cn", "title_cn"))
        or a.get("public_rights", {}).get("status") != "withheld"
        or a.get("translation", {}).get("status") != "rights_withheld"
        for i, a in enumerate(jae_articles, 1)
    ):
        raise AssertionError("JAE metadata type/order, identity or rights withholding failed")
if expected >= 52:
    itpf = load("itpf-current.json")
    itpf_articles = itpf.get("articles", [])
    expected_itpf_dois = [
        "10.1007/s10797-026-09955-7",
        "10.1007/s10797-026-09958-4",
        "10.1007/s10797-026-09945-9",
        "10.1007/s10797-026-09944-w",
        "10.1007/s10797-026-09949-5",
        "10.1007/s10797-026-09948-6",
        "10.1007/s10797-025-09942-4",
        "10.1007/s10797-025-09932-6",
        "10.1007/s10797-026-09967-3",
        "10.1007/s10797-026-09962-8",
        "10.1007/s10797-025-09928-2",
    ]
    rights = itpf.get("public_content", {})
    q = itpf.get("quality", {})
    if (
        itpf.get("journal_id") != "itpf"
        or itpf.get("issue_id") != "itpf-33-5"
        or itpf.get("source_url") != "https://ideas.repec.org/s/kap/itaxpf.html"
        or itpf.get("source_status") != "publisher_verified"
        or itpf.get("publication_state") != "ready"
        or itpf.get("content_status") != "metadata_complete"
        or itpf.get("public_mode") != "metadata_first_rights_qualified_v1"
        or q.get("roster_authority") != "repec-publisher-supplied"
        or q.get("roster_match") is not True
        or q.get("order_preserved") is not True
        or q.get("excluded_items", [])
        or rights.get("metadata_articles") != 11
        or rights.get("rights_withheld_articles") != 11
        or rights.get("abstracts_en_visible") != 0
        or rights.get("translations_cn_visible") != 0
        or [str(a.get("doi", "")) for a in itpf_articles] != expected_itpf_dois
    ):
        raise AssertionError("ITPF 33(5) publisher-supplied serial order/rights rejected")
    if any(
        a.get("article_type") != "research-article"
        or a.get("sequence") != i
        or not a.get("title_en") or not a.get("authors")
        or any(str(a.get(k, "")).strip() for k in
               ("abstract_en", "abstract_cn", "title_cn"))
        or a.get("public_rights", {}).get("status") != "withheld"
        or a.get("translation", {}).get("status") != "rights_withheld"
        for i, a in enumerate(itpf_articles, 1)
    ):
        raise AssertionError("ITPF article classification/order or rights withholding rejected")
if expected == 53:
    joeg = load("joeg-current.json")
    joeg_articles = joeg.get("articles", [])
    expected_joeg_dois = [
        "10.1093/jeg/lbag036",
        "10.1093/jeg/lbag026",
        "10.1093/jeg/lbag047",
        "10.1093/jeg/lbag041",
        "10.1093/jeg/lbag002",
        "10.1093/jeg/lbag006",
        "10.1093/jeg/lbag008",
        "10.1093/jeg/lbag012",
        "10.1093/jeg/lbag009",
    ]
    joeg_q = joeg.get("quality", {})
    joeg_rights = joeg.get("public_content", {})
    exclusions = joeg_q.get("excluded_items", [])
    if (
        joeg.get("journal_id") != "joeg"
        or joeg.get("issue_id") != "joeg-26-5"
        or joeg.get("source_url") != "https://academic.oup.com/joeg/issue/26/5"
        or joeg.get("source_status") != "official_verified"
        or joeg.get("publication_state") != "ready"
        or joeg.get("content_status") != "metadata_complete"
        or joeg.get("public_mode") != "metadata_first_rights_qualified_v1"
        or joeg_q.get("roster_authority") != "official-issue-page"
        or joeg_q.get("roster_match") is not True
        or joeg_q.get("order_preserved") is not True
        or joeg_q.get("abstract_en_complete") != 0
        or joeg_q.get("translation_complete") != 0
        or joeg_rights.get("metadata_articles") != 9
        or joeg_rights.get("rights_withheld_articles") != 9
        or joeg_rights.get("abstracts_en_visible") != 0
        or joeg_rights.get("translations_cn_visible") != 0
        or [str(a.get("doi", "")) for a in joeg_articles] != expected_joeg_dois
        or len(exclusions) != 1
        or exclusions[0].get("doi") != "10.1093/jeg/lbag061"
        or exclusions[0].get("article_type") != "correction"
    ):
        raise AssertionError("JOEG 26(5) OUP 9/10 ordered issue, comment/correction or rights gate failed")
    joeg_types = ["comment"] * 3 + ["research-article"] * 6
    if any(
        a.get("article_type") != joeg_types[i - 1]
        or a.get("sequence") != i
        or not a.get("title_en") or not a.get("authors")
        or any(str(a.get(k, "")).strip() for k in
               ("abstract_en", "abstract_cn", "title_cn"))
        or a.get("public_rights", {}).get("status") != "withheld"
        or a.get("translation", {}).get("status") != "rights_withheld"
        for i, a in enumerate(joeg_articles, 1)
    ):
        raise AssertionError("JOEG source-attested comment/research types or public rights rejected")
if not summary.get("articles"):
    raise AssertionError("public article totals are empty")
if expected <= 50:
    if health.get("content_status") != "complete":
        raise AssertionError("legacy journal translations are incomplete")
    if summary.get("translated_articles") != summary.get("articles"):
        raise AssertionError("legacy article and translation totals do not reconcile")
else:
    # Approved B8 metadata-first release withholds exactly 12
    # unlicensed English abstracts and Chinese adaptations.
    if health.get("content_status") != "rights_qualified":
        raise AssertionError("metadata-first rights-qualified status missing")
    expected_withheld = {51: 12, 52: 23, 53: 32}[expected]
    if summary.get("rights_withheld_articles") != expected_withheld:
        raise AssertionError("rights-withheld public article count drift")
    if summary.get("translated_articles") + expected_withheld != summary.get("articles"):
        raise AssertionError("legacy translations plus withheld JAE rows do not reconcile")
failed_checks = [
    key for key, value in health.get("checks", {}).items() if value is False
]
hard_failures = [
    key for key in failed_checks if not key.endswith("_primary_transport")
]
if hard_failures:
    raise AssertionError("structural health checks failed: " + ", ".join(hard_failures))
if health.get("status") not in {"healthy", "degraded"}:
    raise AssertionError("health status is invalid")
health_stamp = timestamp(health, "health", 6)

monitor_summary = monitoring.get("summary", {})
if monitoring.get("status") != "healthy":
    # A newly admitted source-attested JAE is not falsely reported
    # as operationally probed. Accept ONLY its explicit single
    # warning while the first monitor observation is outstanding.
    pending_jae = (
        expected in (51, 52, 53)
        and monitoring.get("status") == "degraded"
        and monitoring.get("warning_journals") == [
            "JAE" if expected == 51 else ("ITPF" if expected == 52 else "JOEG")
        ]
        and monitor_summary.get("warnings") == 1
        and monitor_summary.get("failed") == 0
    )
    if not pending_jae:
        raise AssertionError("journal monitor has unqualified degradation")
if monitor_summary.get("configured_journals") != expected:
    raise AssertionError("monitor configured_journals differs from approved live scope")
if monitor_summary.get("failed") != 0:
    raise AssertionError("journal monitor has failed journals")
timestamp(monitoring, "monitoring", 6)

source_summary = source.get("summary", {})
if source.get("status") != "healthy":
    raise AssertionError("source audit is not healthy")
if source_summary.get("configured_journals") != expected:
    raise AssertionError("source audit configured_journals differs from approved live scope")
for key in ("needs_attention", "missing", "invariant_errors"):
    if source_summary.get(key) != 0:
        raise AssertionError(f"source audit {key} is not zero")
timestamp(source, "source-audit", 6)

# The 19 primary-transport failures are an independent source
# availability signal, NOT missing journal records. Check that
# diagnostics report the same boolean truth as health.json.
if diagnostics.get("schema_version") != "1.0":
    raise AssertionError("source diagnostics schema is invalid")
timestamp_field(diagnostics, "source-diagnostics", "generated_at", 6)
records = diagnostics.get("journals")
if not isinstance(records, list) or len(records) != expected:
    raise AssertionError("source diagnostics count must equal all enabled journals")
reported_transport = {}
for item in records:
    if not isinstance(item, dict):
        raise AssertionError("source diagnostic journal record is invalid")
    jid = str(item.get("journal_id") or "")
    key = f"{jid}_primary_transport"
    if not jid or jid in reported_transport or key not in health.get("checks", {}):
        raise AssertionError("source diagnostics identity mismatch")
    primary = item.get("primary_transport_available")
    if not isinstance(primary, bool) or primary is not health["checks"][key]:
        raise AssertionError(f"{jid}: source transport disagrees with health")
    reported_transport[jid] = primary
    if item.get("publisher_coverage") not in {
        "not_automatically_checked", "reconciled_as_of",
        "publisher_changed", "verification_due",
        "verification_blocked",
    }:
        raise AssertionError(f"{jid}: unknown publisher coverage status")
    # An old first-party snapshot may be reconciled *as of* its
    # observation, but a newly generated report is not a new
    # publisher observation. Never infer completeness from READY.
    if item.get("publisher_coverage") == "reconciled_as_of":
        observed = item.get("publisher_observed_at")
        if not observed:
            raise AssertionError(f"{jid}: verified without source time")
        stamp = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp > now:
            raise AssertionError(f"{jid}: invalid publisher observation")
if expected >= 50 and "jegrowth" not in reported_transport:
    raise AssertionError("newly enabled journal missing from source diagnostics")
unavailable = sum(not x for x in reported_transport.values())
diag_summary = diagnostics.get("summary", {})
if diag_summary.get("journals") != expected or (
    diag_summary.get("primary_unavailable") != unavailable
    or diag_summary.get("primary_available") != expected - unavailable
):
    raise AssertionError("source diagnostics summary does not reconcile")

# R4 is currently an observational baseline. Validate publication
# and shape, not artificial source freshness thresholds/coverage.
if slo.get("schema_version") != "1.0" or (
    slo.get("phase") != "baseline_metrics_only"
):
    raise AssertionError("R4 SLO baseline schema/phase is invalid")
if slo.get("alerting_enabled") is not False or (
    slo.get("thresholds_defined") is not False
):
    raise AssertionError("R4 SLO must remain measurement-only")
timestamp_field(slo, "slo", "generated_at", 6)
if slo.get("product_truth", {}).get("journal_count") != expected:
    raise AssertionError("R4 journal count mismatch")
metrics = slo.get("metrics", {})
if metrics.get("monitor_probe_freshness", {}).get(
    "configured_journal_count"
) != expected:
    raise AssertionError("R4 monitor probe journal count mismatch")
for key in (
    "expected_set_freshness",
    "current_issue_authority_freshness",
    "official_detection_to_canonical_ready_latency",
):
    if metrics.get(key, {}).get("measurement_state") not in {
        "direct", "partial", "count_only", "not_yet_measurable",
    }:
        raise AssertionError(f"R4 {key} has no valid measurement state")

if completeness.get("schema_version") != "1.3":
    raise AssertionError("completeness schema_version is not 1.3")
if completeness.get("scope") != "journals_tracked":
    raise AssertionError("completeness scope is invalid")
reconciliation = completeness.get("reconciliation", {})
if reconciliation.get("journal_count") != expected:
    raise AssertionError("completeness journal_count differs from enabled scope")
status_total = sum(
    int(reconciliation.get(key, 0))
    for key in ("complete", "partial", "not_measured", "source_blocked")
)
if status_total != expected:
    raise AssertionError("completeness status counts do not reconcile")
if len(completeness.get("journals", [])) != expected:
    raise AssertionError("completeness journal records do not reconcile")
timestamp_field(completeness, "completeness", "generated_at", 6)

if manifest.get("project_id") != "journals":
    raise AssertionError("project manifest id is invalid")
if manifest.get("journal_count") != expected or manifest.get("available_journal_count") != expected:
    raise AssertionError("project manifest journal counts do not reconcile")
if manifest.get("status") != health.get("status"):
    raise AssertionError("manifest and health status disagree")
manifest_stamp = timestamp(manifest, "project-manifest", 6)
if abs((manifest_stamp - health_stamp).total_seconds()) > 300:
    raise AssertionError("manifest and health timestamps differ by more than five minutes")

feed = (root / "all.xml").read_text(encoding="utf-8")
if "<rss" not in feed or "<item" not in feed:
    raise AssertionError("all-journals RSS feed is empty or malformed")
print(
    f"public semantic health: {expected}/{expected} journals, "
    "rights-qualified metadata where applicable, source diagnostics "
    "and R4 baseline present"
)
