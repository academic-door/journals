"""JIE 163 authoritative publisher order, 8 scholarly + 2 editorials regression."""

import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.import_official_roster_evidence import validate_evidence
from scripts.jie_current_roster import apply_jie_current_roster
import yaml
import scripts.update_journals as update_journals

from scripts.update_journals import (
    ROOT,
    apply_configured_current_roster,
    validate_issue,
)

PATH = "data/provenance/official-rosters/jie/jie-163-c.json"
CONFIG = {"id": "jie", "official_roster_evidence_path": PATH}
EXPECTED_ORDER = [
    "10.1016/j.jinteco.2025.104203", "10.1016/j.jinteco.2025.104207",
    "10.1016/j.jinteco.2025.104204", "10.1016/j.jinteco.2026.104219",
    "10.1016/j.jinteco.2026.104225", "10.1016/j.jinteco.2026.104231",
    "10.1016/j.jinteco.2026.104230", "10.1016/j.jinteco.2026.104290",
]


class JIE163OfficialCurrentTests(unittest.TestCase):
    def setUp(self):
        self.evidence = json.loads((ROOT / PATH).read_text(encoding="utf-8"))
        self.current = json.loads(
            (ROOT / "public/api/v1/journals/jie/issues/current.json").read_text(encoding="utf-8")
        )

    def test_publisher_eight_not_nine_research_two_editorials(self):
        validate_evidence(self.evidence)
        self.assertEqual(8, len(self.evidence["items"]))
        self.assertEqual(2, len(self.evidence["excluded_items"]))
        self.assertEqual(EXPECTED_ORDER, [r["doi"] for r in self.evidence["items"]])
        self.assertEqual(list(range(3, 11)), [r["source_sequence"] for r in self.evidence["items"]])
        self.assertEqual([1, 2], [r["source_sequence"] for r in self.evidence["excluded_items"]])
        self.assertEqual("10.1016/s0022-1996(26)00094-2", self.evidence["excluded_items"][0]["doi"])
        self.assertEqual("10.1016/j.jinteco.2026.104295", self.evidence["excluded_items"][1]["doi"])

    def test_reorder_eight_preserve_all_translations_add_missing_board(self):
        before = copy.deepcopy(self.current)
        restored = apply_jie_current_roster(self.current, CONFIG)
        self.assertEqual(before, self.current)
        self.assertEqual(EXPECTED_ORDER, [a["doi"] for a in restored["articles"]])
        self.assertEqual(8, restored["quality"]["translation_complete"])
        self.assertEqual(10, restored["quality"]["official_item_count"])
        self.assertEqual("official_verified", restored["source_status"])
        self.assertEqual("ready", restored["publication_state"])
        self.assertEqual(8, restored["content_counts"]["research_articles"])
        self.assertEqual(2, restored["content_counts"]["editorial_material"])
        self.assertEqual(2, len(restored["quality"]["excluded_items"]))
        self.assertEqual("official-publisher-issue-full", restored["quality"]["roster_match_scope"])
        old_by_doi = {a["doi"]: a for a in before["articles"]}
        for a in restored["articles"]:
            self.assertEqual(old_by_doi[a["doi"]]["title_cn"], a["title_cn"])
            self.assertEqual(old_by_doi[a["doi"]]["abstract_cn"], a["abstract_cn"])
        validate_issue(restored)

    def test_current_jie_config_routes_only_to_jie_roster(self):
        config = yaml.safe_load(
            (ROOT / "config/journals.yml").read_text(encoding="utf-8")
        )["journals"]["JIE"]
        restored = apply_configured_current_roster(copy.deepcopy(self.current), "JIE", config)
        self.assertEqual(EXPECTED_ORDER, [a["doi"] for a in restored["articles"]])
        self.assertEqual(2, len(restored["quality"]["excluded_items"]))

    def test_null_monitor_entry_keeps_static_jie_roster_routing(self):
        config = yaml.safe_load(
            (ROOT / "config/journals.yml").read_text(encoding="utf-8")
        )["journals"]["JIE"]
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "monitor-state.json"
            state_path.write_text(
                json.dumps({"journals": {"JIE": None}}),
                encoding="utf-8",
            )
            with patch.object(update_journals, "MONITOR_STATE_PATH", state_path):
                restored = apply_configured_current_roster(
                    copy.deepcopy(self.current), "JIE", config
                )
        self.assertEqual(EXPECTED_ORDER, [a["doi"] for a in restored["articles"]])
        self.assertEqual(2, len(restored["quality"]["excluded_items"]))

    def test_collect_one_reuses_matching_translations_before_jie_roster_gate(self):
        recollected = copy.deepcopy(self.current)
        for article in recollected["articles"]:
            article["title_cn"] = ""
            article["abstract_cn"] = ""
            article["translation"] = {"status": "missing"}

        with self.assertRaisesRegex(ValueError, "cannot pass first-party 8\\+2"):
            apply_jie_current_roster(copy.deepcopy(recollected), CONFIG)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_path = root / "current.json"
            current_path.write_text(
                json.dumps(self.current, ensure_ascii=False),
                encoding="utf-8",
            )
            config = {
                **CONFIG,
                "current_issue_url": self.evidence["official_url"],
            }
            with (
                patch.object(update_journals, "public_issue_path", return_value=current_path),
                patch.object(
                    update_journals,
                    "detected_issue_path",
                    return_value=root / "detected.json",
                ),
                patch.object(
                    update_journals,
                    "MONITOR_STATE_PATH",
                    root / "monitor-state.json",
                ),
                patch.object(
                    update_journals,
                    "collector_for",
                    return_value=lambda: copy.deepcopy(recollected),
                ),
                patch.object(update_journals, "fallback_collector_for", return_value=None),
                patch.object(update_journals, "apply_translation_cache", side_effect=lambda issue: issue),
                patch.object(update_journals, "write_detected_snapshot"),
                patch.object(update_journals, "archive_issue"),
            ):
                issue, report = update_journals.collect_one(
                    "JIE",
                    config,
                    translate=False,
                )

        self.assertEqual("updated", report["result"])
        self.assertEqual(EXPECTED_ORDER, [article["doi"] for article in issue["articles"]])
        self.assertEqual(8, issue["quality"]["translation_complete"])
        self.assertEqual(2, len(issue["quality"]["excluded_items"]))
        old_by_doi = {article["doi"]: article for article in self.current["articles"]}
        for article in issue["articles"]:
            self.assertEqual(old_by_doi[article["doi"]]["title_cn"], article["title_cn"])
            self.assertEqual(old_by_doi[article["doi"]]["abstract_cn"], article["abstract_cn"])
            self.assertEqual("complete", article["translation"]["status"])

    def test_wrong_candidate_old_issue_and_wrong_journal_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "not in official publisher"):
            apply_jie_current_roster(self.current, CONFIG, detected_dois=["10.1016/fake"])
        prior = copy.deepcopy(self.current)
        prior["issue_id"], prior["volume"] = "jie-162-c", "162"
        self.assertEqual(prior, apply_jie_current_roster(prior, CONFIG))
        other = copy.deepcopy(self.current)
        other["journal_id"] = "jeem"
        with self.assertRaisesRegex(ValueError, "another journal"):
            apply_jie_current_roster(other, CONFIG)

    def test_publisher_doi_order_and_exclusion_mutations_are_blocked(self):
        mutations = (
            lambda e: e["items"][0].update(doi="10.1016/j.jinteco.fake"),
            lambda e: e["items"][0].update(source_sequence=4),
            lambda e: e["items"][0].update(source_id="pii:S0022199626000942"),
            lambda e: e["excluded_items"][0].update(article_type="research-article"),
            lambda e: e.update(captured_at="2999-01-01T00:00:00Z"),
        )
        for mutate in mutations:
            with self.subTest(mutation=repr(mutate)):
                ev = copy.deepcopy(self.evidence)
                mutate(ev)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / PATH
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        apply_jie_current_roster(self.current, CONFIG, root=Path(directory))


if __name__ == "__main__":
    unittest.main()
