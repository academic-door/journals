from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.import_official_roster_evidence import apply_evidence, validate_evidence
from scripts.official_current_roster import apply_current_roster
from scripts.update_journals import ROOT, collect_one, validate_issue


EVIDENCE_PATH = "data/provenance/official-rosters/sciencedirect/energy-163-c.json"
RAW_PATH = "data/provenance/browser-snapshots/sciencedirect/energy-163-c.json"
CONFIG = {"id": "energy", "name": "Energy Economics", "collector": "elsevier",
          "current_issue_url": "https://www.sciencedirect.com/journal/energy-economics/issues",
          "official_roster_evidence_path": EVIDENCE_PATH}


class OfficialCurrentRosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = json.loads((ROOT / EVIDENCE_PATH).read_text(encoding="utf-8"))
        self.raw = json.loads((ROOT / RAW_PATH).read_text(encoding="utf-8"))
        self.dois = [row["doi"] for row in self.raw["items"]]
        empty = {
            "schema_version": "1.0", "journal_id": "energy", "journal_name": "Energy Economics",
            "issue_id": "energy-163-c", "volume": "163", "issue": "C",
            "publication_date": "November 2026", "source_url": self.evidence["official_url"],
            "retrieved_at": self.evidence["captured_at"], "expected_article_count": 0,
            "research_article_count": 0, "status": "incomplete", "articles": [],
            "quality": {"flags": [], "excluded_items": [], "duplicate_count": 0},
        }
        self.issue = apply_evidence(empty, self.evidence)
        self.issue["quality"]["excluded_items"] = copy.deepcopy(self.evidence["excluded_items"])
        self.issue["articles"].reverse()
        self.issue["quality"]["flags"] += ["publisher_rss_reverse_order_normalized", "official_order_unverified"]
        self.issue["quality"]["roster_authority"] = "publisher-rss"

    def apply(self, issue: dict | None = None) -> dict:
        return apply_current_roster(issue or self.issue, CONFIG, detected_dois=self.dois)

    def with_evidence(self, mutation) -> dict:
        evidence = copy.deepcopy(self.evidence)
        mutation(evidence)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / EVIDENCE_PATH
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(evidence), encoding="utf-8")
            return apply_current_roster(self.issue, CONFIG, root=root, detected_dois=self.dois)

    def test_real_capture_has_34_rows_33_research_and_one_corrigendum(self) -> None:
        validate_evidence(self.evidence)
        self.assertEqual(len(self.raw["items"]), 34)
        self.assertEqual(len(self.evidence["items"]), 33)
        self.assertEqual(self.evidence["excluded_items"][0]["doi"], "10.1016/j.eneco.2026.109622")
        self.assertEqual(self.evidence["excluded_items"][0]["source_sequence"], 28)
        self.assertEqual(self.raw["publisher_issue_status"], "in-progress")
        self.assertEqual(self.evidence["captured_at"], self.raw["captured_at"])
        for row in self.evidence["items"]:
            captured = self.raw["items"][row["source_sequence"] - 1]
            self.assertEqual(row["doi"], captured["doi"])
            self.assertEqual(row["source_id"], "pii:" + captured["href"].rsplit("/", 1)[-1])
            self.assertEqual(row["title_en"], captured["title"])
            self.assertEqual(row["abstract_en"], captured["abstract_en"])
            self.assertEqual(row["doi_source"], captured["doi_source"])

    def test_official_order_replaces_rss_order_without_forcing_ready(self) -> None:
        original = copy.deepcopy(self.issue)
        result = self.apply()
        self.assertEqual([a["doi"] for a in result["articles"]], [r["doi"] for r in self.evidence["items"]])
        self.assertEqual(result["source_status"], "official_verified")
        self.assertEqual(result["publication_state"], "translation_partial")
        self.assertEqual(result["quality"]["translation_complete"], 0)
        self.assertEqual(result["quality"]["official_item_count"], 34)
        self.assertEqual(result["publisher_issue_status"], "in-progress")
        self.assertNotIn("publisher_rss_reverse_order_normalized", result["quality"]["flags"])
        self.assertEqual(self.issue, original)
        self.assertEqual(result["quality"]["official_roster_evidence"]["captured_at"], self.evidence["captured_at"])
        self.assertEqual([a["source_sequence"] for a in result["articles"]][26:29], [27, 29, 30])
        validate_issue(result)

    def test_lagging_feed_restores_only_observed_missing_article(self) -> None:
        issue = copy.deepcopy(self.issue)
        issue["articles"] = [a for a in issue["articles"] if a["doi"] != "10.1016/j.eneco.2026.109641"]
        result = self.apply(issue)
        self.assertEqual(len(result["articles"]), 33)
        added = next(a for a in result["articles"] if a["doi"].endswith("109641"))
        self.assertEqual(added["sources"]["abstract_en"], self.evidence["official_url"])
        self.assertEqual(added["sources"]["doi"], "crossref-publisher-deposit")
        self.assertEqual(added["translation"]["status"], "missing")

    def test_only_missing_metadata_is_filled(self) -> None:
        issue = copy.deepcopy(self.issue)
        issue["articles"][0]["authors"] = []
        issue["articles"][0]["abstract_en"] = ""
        original = copy.deepcopy(issue["articles"][1])
        result = self.apply(issue)
        filled = next(a for a in result["articles"] if a["doi"] == issue["articles"][0]["doi"])
        preserved = next(a for a in result["articles"] if a["doi"] == original["doi"])
        self.assertTrue(filled["authors"])
        self.assertTrue(filled["abstract_en"])
        self.assertEqual(preserved["abstract_en"], original["abstract_en"])
        self.assertEqual(preserved["authors"], original["authors"])
        issue["articles"][0]["abstract_en"] = "No abstract available"
        result = self.apply(issue)
        filled = next(a for a in result["articles"] if a["doi"] == issue["articles"][0]["doi"])
        self.assertNotEqual(filled["abstract_en"], "No abstract available")

    def test_changed_detector_or_extra_collector_doi_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "detected DOI set changed"):
            apply_current_roster(self.issue, CONFIG, detected_dois=self.dois + ["10.1016/j.eneco.2026.999999"])
        issue = copy.deepcopy(self.issue)
        issue["articles"][0]["doi"] = "10.1016/j.eneco.2026.999999"
        with self.assertRaisesRegex(ValueError, "unobserved research DOI"):
            self.apply(issue)

    def test_title_or_pii_mismatch_is_rejected(self) -> None:
        for field, value in (("title_en", "A different article"), ("source_url", "https://www.sciencedirect.com/science/article/pii/S0000000000000000")):
            issue = copy.deepcopy(self.issue)
            issue["articles"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "contradicts official evidence"):
                self.apply(issue)

    def test_duplicate_collector_doi_is_rejected(self) -> None:
        issue = copy.deepcopy(self.issue)
        issue["articles"].append(copy.deepcopy(issue["articles"][0]))
        with self.assertRaisesRegex(ValueError, "duplicates"):
            self.apply(issue)

    def test_unobserved_exclusion_is_rejected(self) -> None:
        issue = copy.deepcopy(self.issue)
        issue["quality"]["excluded_items"][0]["doi"] = "10.1016/j.eneco.2026.999999"
        issue["quality"]["excluded_items"][0]["source_id"] = "pii:S0000000000000000"
        issue["quality"]["excluded_items"][0]["source_url"] = ""
        with self.assertRaisesRegex(ValueError, "exclusion contradicts"):
            self.apply(issue)

    def test_evidence_gaps_wrong_order_and_private_fields_are_rejected(self) -> None:
        mutations = [
            lambda e: e["items"][0].update(source_sequence=2),
            lambda e: e["items"][0].update(source_id=e["items"][1]["source_id"]),
            lambda e: e["items"][0].update(abstract_en=""),
            lambda e: e.update(captured_at="2999-01-01T00:00:00Z"),
            lambda e: e.update(api_token="synthetic-forbidden-field"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.with_evidence(mutation)

    def test_path_escape_is_rejected_and_other_issue_is_unchanged(self) -> None:
        with self.assertRaisesRegex(ValueError, "official provenance"):
            apply_current_roster(self.issue, {**CONFIG, "official_roster_evidence_path": "../outside.json"})
        issue = {**self.issue, "issue_id": "energy-164-c", "volume": "164"}
        self.assertIs(apply_current_roster(issue, CONFIG), issue)
        self.assertIs(apply_current_roster(self.issue, {"id": "energy"}), self.issue)

    def test_collect_one_preserves_last_good_until_translations_pass(self) -> None:
        import scripts.update_journals as update

        previous = json.loads((ROOT / "public/api/v1/journals/energy/issues/current.json").read_text(encoding="utf-8"))
        previous["issue_id"], previous["volume"] = "energy-162-c", "162"
        with tempfile.TemporaryDirectory() as directory:
            api = Path(directory) / "api"
            target = api / "journals/energy/issues/current.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(previous), encoding="utf-8")
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"journals": {"ENERGY": {"candidate": {"volume": "163", "unseen_dois": self.dois}}}}), encoding="utf-8")
            with mock.patch.object(update, "PUBLIC_API", api), mock.patch.object(update, "MONITOR_STATE_PATH", state), mock.patch.object(update, "collector_for", return_value=lambda: copy.deepcopy(self.issue)), mock.patch.object(update, "fallback_collector_for", return_value=None):
                result, report = collect_one("ENERGY", CONFIG, translate=False)
            self.assertEqual(result["issue_id"], "energy-162-c")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), previous)
            # Real translation-cache overlays can reuse any number of the
            # 33 source-identical translations. A partial result must still
            # preserve the last READY issue; it need not remain at 0/33.
            detected = json.loads((target.parent / "detected.json").read_text(encoding="utf-8"))
            translated = detected["quality"]["translation_complete"]
            self.assertGreaterEqual(translated, 0)
            self.assertLess(translated, 33)
            self.assertIn(f"translation incomplete: {translated}/33", report["error"])
            self.assertIn("preserving the last complete public issue", report["error"])
            self.assertEqual(detected["source_status"], "official_verified")
            self.assertNotEqual(detected["publication_state"], "ready")

    def test_monitor_delta_is_reconstructed_from_same_issue_baseline(self) -> None:
        import scripts.update_journals as update

        baseline = copy.deepcopy(self.issue)
        baseline["articles"] = [a for a in baseline["articles"] if not a["doi"].endswith("109641")]
        with tempfile.TemporaryDirectory() as directory:
            api = Path(directory) / "api"
            target = api / "journals/energy/issues/current.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(baseline), encoding="utf-8")
            state = Path(directory) / "state.json"
            candidate = {"volume": "163", "doi_count": 33, "unseen_dois": ["10.1016/j.eneco.2026.109641"]}
            state.write_text(json.dumps({"journals": {"ENERGY": {"candidate": candidate}}}), encoding="utf-8")
            with mock.patch.object(update, "PUBLIC_API", api), mock.patch.object(update, "MONITOR_STATE_PATH", state):
                result = update.apply_configured_current_roster(self.issue, "ENERGY", CONFIG)
                self.assertEqual(len(result["articles"]), 33)
                candidate["unseen_dois"] = ["10.1016/j.eneco.2026.999999"]
                state.write_text(json.dumps({"journals": {"ENERGY": {"candidate": candidate}}}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "detected DOI set changed"):
                    update.apply_configured_current_roster(self.issue, "ENERGY", CONFIG)

    def test_missing_rss_dois_restored_only_by_exact_official_pii_title(self):
        issue = copy.deepcopy(self.issue)
        for article in issue["articles"][:4]:
            article["doi"] = ""
            article["paper_id"] = "pii:" + article["source_url"].rsplit("/", 1)[-1]
        result = self.apply(issue)
        self.assertEqual(result["quality"]["doi_complete"], 33)
        self.assertEqual(len({a["doi"] for a in result["articles"]}), 33)
        self.assertNotEqual(result["publication_state"], "ready")
        for kind in ("pii", "title", "duplicate"):
            bad = copy.deepcopy(issue)
            if kind == "pii": bad["articles"][0]["source_url"] = "https://www.sciencedirect.com/science/article/pii/S0000000000000000"
            if kind == "title": bad["articles"][0]["title_en"] = "Unknown title"
            if kind == "duplicate": bad["articles"].append(copy.deepcopy(bad["articles"][0]))
            with self.assertRaises(ValueError): self.apply(bad)


if __name__ == "__main__":
    unittest.main()
