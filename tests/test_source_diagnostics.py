from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_source_diagnostics import build_report, build_from_tree
from scripts.journal_monitor import select_candidate, evaluate_observation

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/publisher_coverage_issue_diagnostics.json"


class SourceDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "JEEM": {"id": "jeem", "enabled": True, "official_roster_evidence_path": "roster.json"},
            "JIE": {"id": "jie", "enabled": True, "official_roster_evidence_path": "roster.json"},
            "QE": {"id": "qe", "enabled": True},
        }
        self.base_issue = {
            "issue_id": "jeem-140-c",
            "volume": "140",
            "issue": "C",
            "publication_state": "ready",
            "content_status": "complete",
            "articles": [{"doi": "10.1016/j.jeem.2026.103397"}],
            "quality": {
                "excluded_items": [],
                "roster_transport": "official-page-read",
                "roster_match_scope": "official-publisher-issue-full",
                "official_roster_evidence": {"captured_at": "2026-10-18T17:23:14Z"},
            },
        }
        self.issues = {
            "jeem": copy.deepcopy(self.base_issue),
            "jie": {
                "issue_id": "jie-163-c",
                "publication_state": "ready",
                "content_status": "complete",
                "articles": [],
                "quality": {"roster_transport": "sciencedirect-rss", "excluded_items": []},
            },
            "qe": {
                "issue_id": "qe-17-3",
                "publication_state": "ready",
                "content_status": "complete",
                "articles": [],
                "quality": {"flags": ["repec_publisher_supplied_roster"]},
            },
        }
        self.health = {
            "checks": {
                "jeem_primary_transport": False,
                "jie_primary_transport": False,
                "qe_primary_transport": False,
            }
        }
        self.generated = "2026-10-25T12:00:00+00:00"

    def report(self, rosters=None):
        return build_report(
            self.cfg, self.issues, self.health, rosters or {},
            generated_at=self.generated,
        )

    def test_classifies_fallbacks_without_claiming_broken_archives(self):
        report = self.report()
        self.assertEqual(3, report["summary"]["primary_unavailable"])
        a, b, c = report["journals"]
        self.assertEqual("stored_first_party_snapshot_primary_unavailable", a["source_route"])
        self.assertEqual("publisher_rss_fallback", b["source_route"])
        self.assertEqual("publisher_supplied_repec_fallback", c["source_route"])
        self.assertTrue(all(v["archive_ready"] for v in report["journals"]))
        self.assertEqual("verification_due", a["publisher_coverage"])
        self.assertEqual("verification_due", b["publisher_coverage"])
        self.assertEqual("not_automatically_checked", c["publisher_coverage"])

    def test_stale_capture_cannot_certify_a_different_issue(self):
        old = {
            "journal_id": "jeem", "issue_id": "jeem-139-c",
            "captured_at": "2026-10-18T17:23:14Z",
        }
        result = self.report({"jeem": old})
        self.assertEqual("verification_due", result["journals"][0]["publisher_coverage"])
        self.assertIsNone(result["journals"][0]["publisher_observed_at"])

    def test_blocked_evidence_not_relabelled_as_reconciled(self):
        bad = {
            "journal_id": "jeem", "issue_id": "jeem-140-c",
            "source_error": "HTTP 403", "method": "publisher-403",
            "captured_at": "2026-10-18T17:23:14Z",
        }
        result = self.report({"jeem": bad})
        self.assertEqual("verification_blocked", result["journals"][0]["publisher_coverage"])
        self.assertIsNone(result["journals"][0]["publisher_observed_at"])
        self.assertNotEqual(self.generated, result["journals"][0]["last_verified_at"])

    def test_direct_script_invocation_matches_monitor_workflow(self):
        # CI module imports succeed with repository root on sys.path; the real
        # monitor invokes the source file directly and must work as well.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source-diagnostics.json"
            completed = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts/build_source_diagnostics.py"),
                    "--api-root", str(ROOT / "public/api/v1"),
                    "--out", str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(53, data["summary"]["journals"])

    def test_existing_three_owner_capture_files_compare_without_network(self):
        # Uses real owner evidence, not the diagnostic-only DOI fixture.
        api = ROOT / "public/api/v1"
        report = build_from_tree(ROOT, api, generated_at=self.generated)
        self.assertEqual(53, report["summary"]["journals"])
        self.assertEqual(53, len(report["journals"]))
        pilots = [x for x in report["journals"] if x["journal_id"] in {"jeem", "jie", "energy"}]
        self.assertEqual(3, len(pilots))
        self.assertTrue(all(x["publisher_coverage"] in {
            "reconciled_as_of", "publisher_changed", "verification_due", "verification_blocked",
        } for x in pilots))
        self.assertEqual(self.generated, report["generated_at"])
        self.assertTrue(all(x["publisher_observed_at"] != self.generated for x in pilots))


class ReplayedSameIssueRegressions(unittest.TestCase):
    def test_jeem_140_historical_four_to_nine_triggers_five_unseen(self):
        fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))["jeem_140"]
        old = fixture["archive"]
        official_dois = [row["doi"] for row in fixture["publisher"]["items"]]
        # Reconstruct Crossref's same-issue matching rows from factual issue DOI
        # identities only. This is an offline replay, NOT a new Crossref read.
        records = [{
            "DOI": doi, "type": "journal-article",
            "title": ["Research result"],
            "volume": "140", "issue": "C",
            "published": {"date-parts": [[2026, 9, 1]]},
        } for doi in official_dois]
        from datetime import date
        candidate = select_candidate(records, {
            **old, "volume": "140", "issue": "C",
        }, today=date(2026, 10, 25))
        self.assertIsNotNone(candidate)
        self.assertEqual(5, len(candidate.unseen_dois))
        status, _ = evaluate_observation(candidate, {
            **old, "volume": "140", "issue": "C",
        }, {})
        self.assertEqual("confirmed", status)

    def test_jie_eight_research_does_not_promote_two_editorial_dois(self):
        fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))["jie_163"]
        old = fixture["archive"]
        official = fixture["publisher"]
        self.assertEqual(8, len(official["items"]))
        self.assertEqual(2, len(official["excluded_items"]))
        from datetime import date
        records = [{
            "DOI": row["doi"], "type": "journal-article",
            "title": [row.get("title_en") or "Research result"],
            "volume": "163", "issue": "C",
            "published": {"date-parts": [[2026, 10, 1]]},
        } for row in official["items"]]
        baseline = {
            **old, "volume": "163", "issue": "C",
            "quality": {"excluded_items": official["excluded_items"]},
        }
        self.assertIsNone(select_candidate(records, baseline, today=date(2026, 10, 25)))
