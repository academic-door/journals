from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
PUBLIC_API = PUBLIC / "api" / "v1"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class PublicSnapshotConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = yaml.safe_load(
            (ROOT / "config" / "journals.yml").read_text(encoding="utf-8")
        )["journals"]
        cls.enabled = {
            key: journal
            for key, journal in cls.config.items()
            if journal.get("enabled")
        }

    def _b8_growth_status(self) -> tuple[bool, bool]:
        """Exactly one approved source-seeded candidate may precede activation.

        The canonical data branch can receive one READY JEGROWTH payload while
        the original 49-title production configuration remains in service.
        After guarded config promotion, old 49-title fallback indexes may also
        exist briefly in a PR checkout. Other or incomplete extras fail closed.
        """
        manifest = load_json(ROOT / "config" / "journal-expansion-b8.json")
        growth = next((j for j in manifest.get("journals", []) if j.get("key") == "JEGROWTH"), {})
        staged = (
            manifest.get("launch_state") == "staging_only"
            and growth.get("rollout_status") == "not_enabled"
            and "JEGROWTH" not in self.enabled
        )
        pending = (
            manifest.get("launch_state") == "partial_rollout_pending_verification"
            and growth.get("rollout_status") == "activation_pending_production"
            and "JEGROWTH" in self.enabled
            and len(self.enabled) == 50
        )
        if not (staged or pending):
            return False, False
        if staged and len(self.enabled) != 49:
            raise AssertionError("B8 original 49-title baseline changed")
        return staged, pending

    def _assert_source_accepted_growth(self) -> None:
        from scripts.seed_b8_jegrowth_release import EXPECTED_DOIS
        from scripts.update_journals import (
            is_archivable_snapshot, issue_translation_semantics_valid,
            issue_source_status, validate_issue,
        )
        issue = load_json(PUBLIC_API / "journals/jegrowth/issues/current.json")
        validate_issue(issue)
        self.assertEqual("jegrowth-31-3", issue.get("issue_id"))
        self.assertEqual("official_verified", issue_source_status(issue))
        self.assertEqual("ready", issue.get("publication_state"))
        self.assertEqual(tuple(a.get("doi") for a in issue["articles"]), EXPECTED_DOIS)
        self.assertEqual(3, issue["quality"].get("translation_complete"))
        self.assertTrue(is_archivable_snapshot(issue))
        self.assertTrue(issue_translation_semantics_valid(issue))

    def _b8_pending_id(self) -> str | None:
        manifest = load_json(ROOT / "config" / "journal-expansion-b8.json")
        pending = [
            j for j in manifest.get("journals", [])
            if j.get("rollout_status") == "activation_pending_production"
        ]
        if len(pending) > 1:
            raise AssertionError("only one B8 title may be pending")
        if not pending:
            return None
        item = pending[0]
        if item["key"] not in self.enabled or self.enabled[item["key"]]["id"] != item["id"]:
            raise AssertionError("pending B8 title is not correctly enabled")
        return item["id"]

    def test_current_snapshots_cover_all_enabled_journals_except_exact_b8_transition(self) -> None:
        configured_ids = {journal["id"] for journal in self.enabled.values()}
        checked_in_ids = {
            path.parents[1].name
            for path in (PUBLIC_API / "journals").glob("*/issues/current.json")
        }
        staged, pending = self._b8_growth_status()
        extras = checked_in_ids - configured_ids
        missing = configured_ids - checked_in_ids
        # The human-approved B8 metadata-first rollout can stage *one*
        # rights-withheld, source-qualified public issue before configuration
        # promotion. Never admit two overlapping pending data publications.
        metadata_extras = extras - {"jegrowth"}
        self.assertLessEqual(len(metadata_extras), 1, "multiple B8 metadata seeds pending")
        if metadata_extras:
            from scripts.b8_rights_qualified import validate_public_projection
            from scripts.update_journals import is_publishable_snapshot
            from scripts.seed_b8_rights_qualified import SPECS
            manifest = load_json(ROOT / "config/journal-expansion-b8.json")
            approved = {
                item["id"]: item for item in manifest["journals"]
                if item["rollout_status"] == "not_enabled"
                and item["key"] in SPECS
            }
            for journal_id in metadata_extras:
                self.assertIn(journal_id, approved, "unexpected new staged journal")
                issue = load_json(PUBLIC_API / "journals" / journal_id / "issues/current.json")
                self.assertEqual(SPECS[approved[journal_id]["key"]][1].casefold(),
                                 issue["issue_id"].casefold())
                validate_public_projection(issue)
                self.assertTrue(is_publishable_snapshot(issue))
                self.assertTrue(all(
                    a["public_rights"]["status"] == "withheld"
                    for a in issue["articles"]
                ))
        pending_id = self._b8_pending_id()
        allowed_missing = {"jegrowth"} | ({pending_id} if pending_id else set())
        self.assertFalse(missing - allowed_missing)
        if "jegrowth" in extras:
            self.assertTrue(staged, "unexpected JEGROWTH data publication")
            self._assert_source_accepted_growth()
        if missing:
            self.assertTrue(
                pending or (pending_id is not None and missing == {pending_id}),
                "enabled journal has no guarded source-qualified current issue",
            )
            # A main-only PR fallback may predate the data-branch derived index.
            # Data-branch overlays and Pages independently rebuild it.
        self.assertEqual(configured_ids - missing | extras, checked_in_ids)

    def test_public_collections_partition_the_enabled_journals(self) -> None:
        memberships: list[str] = []
        for collection_id in ("top5", "fields"):
            payload = load_json(
                PUBLIC_API / "collections" / f"{collection_id}.json"
            )
            memberships.extend(
                journal["journal_id"] for journal in payload["journals"]
            )
        self.assertEqual(len(memberships), len(set(memberships)))
        configured = {journal["id"] for journal in self.enabled.values()}
        actual = set(memberships)
        self.assertFalse(actual - configured, "unapproved journal membership")
        missing = configured - actual
        pending_id = self._b8_pending_id()
        self.assertFalse(
            missing - ({"jegrowth"} | ({pending_id} if pending_id else set())),
            "enabled journals missing from public collections",
        )
        if missing:
            self.assertTrue(
                self._b8_growth_status()[1] or
                (pending_id is not None and missing == {pending_id}),
                "missing collection journal without B8 transition",
            )
        self.assertEqual(len(actual), len(configured) - len(missing))

    def test_health_monitoring_manifest_and_docs_use_same_counts(self) -> None:
        health = load_json(PUBLIC_API / "health.json")
        monitoring = load_json(PUBLIC_API / "monitoring.json")
        manifest = load_json(PUBLIC / "project-manifest.json")
        fields = load_json(PUBLIC_API / "collections" / "fields.json")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        monitoring_keys = (
            set(monitoring.get("last_successful_checks", {}))
            | set(monitoring.get("warning_journals", []))
            | set(monitoring.get("failed_journals", []))
            | set(monitoring.get("awaiting_official_journals", []))
        )
        enabled_count = len(self.enabled)
        reported_count = health["summary"]["enabled_journals"]
        missing = enabled_count - reported_count
        self.assertIn(missing, (0, 1), "health under-reports multiple journals")
        pending_id = self._b8_pending_id()
        if missing:
            self.assertTrue(
                self._b8_growth_status()[1] or pending_id is not None,
                "health may lag only guarded B8 promotion",
            )
        self.assertEqual(reported_count, health["summary"]["available_journals"])
        self.assertEqual(reported_count, monitoring["summary"]["configured_journals"])
        self.assertEqual(reported_count, manifest["journal_count"])
        self.assertEqual(reported_count - 5, manifest["field_journal_count"])
        self.assertEqual(reported_count - 5, fields["summary"]["configured_journals"])
        keys = set(self.enabled)
        if missing:
            if pending_id is not None:
                key = next(k for k, v in self.enabled.items() if v["id"] == pending_id)
                keys.discard(key)
            else:
                keys.discard("JEGROWTH")
        self.assertEqual(keys, monitoring_keys)
        # README announces configured product scope; the prior 49-count
        # remains valid on staging-only main, updated to 50 on activation.
        self.assertIn(f"全站合计 {enabled_count} 本", readme)

    def test_editorial_introduction_is_not_a_research_article(self) -> None:
        """Current snapshots must never publish a known editorial as research.

        The previous assertion additionally required the *current* EJ issue to
        contain exactly one excluded item titled "Introduction by the Editor".
        That made the production monitor fail whenever EJ legitimately moved to
        an issue without that editorial.  Article-type fixture tests cover the
        positive classification rule; this repository-wide consistency guard
        only enforces the invariant that the editorial cannot enter research.
        """

        for path in (PUBLIC_API / "journals").glob("*/issues/current.json"):
            issue = load_json(path)
            included_titles = {
                article.get("title_en", "") for article in issue.get("articles", [])
            }
            self.assertNotIn(
                "Introduction by the Editor",
                included_titles,
                path.as_posix(),
            )
            for item in issue.get("quality", {}).get("excluded_items", []):
                if item.get("title_en") == "Introduction by the Editor":
                    self.assertEqual(
                        "editorial",
                        item.get("article_type"),
                        path.as_posix(),
                    )

    def test_current_issue_dates_do_not_predate_their_articles(self) -> None:
        for path in (PUBLIC_API / "journals").glob("*/issues/current.json"):
            issue = load_json(path)
            issue_year = re.search(
                r"\b(20\d{2})\b", issue.get("publication_date", "")
            )
            article_years = [
                int(match.group(1))
                for article in issue.get("articles", [])
                if (
                    match := re.search(
                        r"\b(20\d{2})\b",
                        article.get("publication_date", ""),
                    )
                )
            ]
            if issue_year and article_years:
                self.assertGreaterEqual(
                    int(issue_year.group(1)),
                    max(article_years),
                    path.as_posix(),
                )


if __name__ == "__main__":
    unittest.main()
