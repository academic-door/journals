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

    def test_main_contains_one_current_snapshot_for_all_49_journals(self) -> None:
        configured_ids = {journal["id"] for journal in self.enabled.values()}
        checked_in_ids = {
            path.parents[1].name
            for path in (PUBLIC_API / "journals").glob("*/issues/current.json")
        }
        self.assertEqual(49, len(configured_ids))
        self.assertEqual(configured_ids, checked_in_ids)

    def test_public_collections_partition_the_enabled_journals(self) -> None:
        memberships: list[str] = []
        for collection_id in ("top5", "fields"):
            payload = load_json(
                PUBLIC_API / "collections" / f"{collection_id}.json"
            )
            memberships.extend(
                journal["journal_id"] for journal in payload["journals"]
            )
        self.assertEqual(49, len(memberships))
        self.assertEqual(49, len(set(memberships)))
        self.assertEqual(
            {journal["id"] for journal in self.enabled.values()},
            set(memberships),
        )

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

        self.assertEqual(49, health["summary"]["enabled_journals"])
        self.assertEqual(49, health["summary"]["available_journals"])
        self.assertEqual(49, monitoring["summary"]["configured_journals"])
        self.assertEqual(set(self.enabled), monitoring_keys)
        self.assertEqual(49, manifest["journal_count"])
        self.assertEqual(44, manifest["field_journal_count"])
        self.assertEqual(44, fields["summary"]["configured_journals"])
        self.assertIn("44 本高水平经济学领域期刊", readme)
        self.assertIn("全站合计 49 本", readme)

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
