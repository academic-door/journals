from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collectors.history import HistoricalIssue
from scripts.backfill_history import (
    atomic_write_json,
    collector_for_issue,
    history_completeness_block,
    run_issue,
)


class BackfillHistoryTests(unittest.TestCase):
    def test_atomic_checkpoint_write_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_write_json(path, {"status": "translation_partial"})
            self.assertEqual(
                {"status": "translation_partial"},
                json.loads(path.read_text(encoding="utf-8")),
            )
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_historical_collector_uses_exact_url_not_current_url(self) -> None:
        config = {
            "id": "aer",
            "name": "American Economic Review",
            "collector": "aea",
            "current_issue_url": "https://www.aeaweb.org/journals/aer/current-issue",
        }
        exact = "https://www.aeaweb.org/issues/828"
        with patch("collectors.aea.fetch_current_issue", return_value={"issue_id": "aer-115-12"}) as fetch:
            result = collector_for_issue(config, exact)()
        self.assertEqual("aer-115-12", result["issue_id"])
        fetch.assert_called_once_with(
            exact,
            journal_id="aer",
            journal_name="American Economic Review",
        )

    def test_issue_identity_is_stable(self) -> None:
        issue = HistoricalIssue("QJE", 2025, "140", "2", "https://academic.oup.com/qje/issue/140/2")
        self.assertEqual("qje-140-2", issue.issue_id)

    def test_wiley_history_uses_publisher_supplied_repec_roster(self) -> None:
        config = {
            "id": "ecta",
            "name": "Econometrica",
            "collector": "wiley",
            "issn": "0012-9682",
            "repec_series_code": "wly/emetrp",
        }
        issue = HistoricalIssue(
            "ECTA",
            2025,
            "93",
            "1",
            "https://onlinelibrary.wiley.com/toc/14680262/2025/93/1",
        )
        with patch(
            "collectors.metadata_fallback.fetch_repec_history_issue",
            return_value={"issue_id": "ecta-93-1"},
        ) as fetch:
            result = collector_for_issue(config, issue)()

        self.assertEqual("ecta-93-1", result["issue_id"])
        fetch.assert_called_once_with(
            journal_id="ecta",
            journal_name="Econometrica",
            issn="0012-9682",
            volume="93",
            issue="1",
            repec_series_code="wly/emetrp",
        )



    def test_elsevier_historical_collector_targets_crossref_volume(self) -> None:
        config = {
            "id": "jde",
            "name": "Journal of Development Economics",
            "collector": "elsevier",
            "issn": "0304-3878",
        }
        ref = HistoricalIssue(
            "JDE",
            2025,
            "172",
            "C",
            "https://www.sciencedirect.com/journal/journal-of-development-economics/vol/172/suppl/C",
        )
        with patch(
            "collectors.metadata_fallback.fetch_crossref_current_issue",
            return_value={"issue_id": "jde-172-c"},
        ) as fetch:
            result = collector_for_issue(config, ref)()
        self.assertEqual("jde-172-c", result["issue_id"])
        fetch.assert_called_once()
        kwargs = fetch.call_args.kwargs
        self.assertEqual("172", kwargs["target_volume"])
        self.assertEqual("", kwargs["target_issue"])
        self.assertEqual("C", kwargs["output_issue"])
        self.assertEqual(2023, kwargs["start_year"])



    def test_history_completeness_guard_blocks_thin_elsevier_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_dir = root / "journals" / "jde" / "issues"
            current_dir.mkdir(parents=True)
            (current_dir / "current.json").write_text(
                json.dumps({"research_article_count": 34}),
                encoding="utf-8",
            )
            reason = history_completeness_block(
                {"research_article_count": 4},
                {"id": "jde"},
                public_api=root,
            )
        self.assertIn("possible_incomplete_volume", reason)
        self.assertIn("4 articles", reason)

    def test_history_completeness_guard_allows_realistic_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_dir = root / "journals" / "jde" / "issues"
            current_dir.mkdir(parents=True)
            (current_dir / "current.json").write_text(
                json.dumps({"research_article_count": 34}),
                encoding="utf-8",
            )
            reason = history_completeness_block(
                {"research_article_count": 30},
                {"id": "jde"},
                public_api=root,
            )
        self.assertEqual("", reason)

    def test_history_completeness_guard_uses_repec_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_dir = root / "journals" / "jde" / "issues"
            current_dir.mkdir(parents=True)
            (current_dir / "current.json").write_text(
                json.dumps({"research_article_count": 34}),
                encoding="utf-8",
            )
            # A genuinely small volume: RePEc lists 6 articles and we collected
            # 6, so the guard must NOT block even though it is far below the
            # current issue size.
            reason = history_completeness_block(
                {
                    "research_article_count": 6,
                    "quality": {"repec_item_count": 6},
                },
                {"id": "jde"},
                public_api=root,
            )
        self.assertEqual("", reason)

    def test_history_completeness_guard_blocks_repec_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reason = history_completeness_block(
                {
                    "research_article_count": 6,
                    "quality": {"repec_item_count": 30},
                },
                {"id": "jde"},
                public_api=root,
            )
        self.assertIn("possible_incomplete_volume", reason)

    def test_history_completeness_guard_allows_small_real_issue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_dir = root / "journals" / "jeem" / "issues"
            current_dir.mkdir(parents=True)
            (current_dir / "current.json").write_text(
                json.dumps({"research_article_count": 4}),
                encoding="utf-8",
            )
            reason = history_completeness_block(
                {"research_article_count": 4},
                {"id": "jeem"},
                public_api=root,
            )
        self.assertEqual("", reason)



    def _complete_elsevier_issue(self) -> dict:
        article = {
            "paper_id": "doi:10.1016/j.jdeveco.2025.100001",
            "sequence": 1,
            "doi": "10.1016/j.jdeveco.2025.100001",
            "title_en": "Paper one",
            "title_cn": "论文一",
            "authors": ["Ada Lovelace"],
            "abstract_en": "Abstract one with 2025 results.",
            "abstract_cn": "摘要一",
            "source_url": "https://www.sciencedirect.com/science/article/pii/S0304387825000001",
            "sources": {"abstract_en": "crossref"},
            "translation": {"status": "complete"},
            "quality_flags": [],
        }
        second = dict(article)
        second["paper_id"] = "doi:10.1016/j.jdeveco.2025.100002"
        second["sequence"] = 2
        second["doi"] = "10.1016/j.jdeveco.2025.100002"
        second["title_en"] = "Paper two"
        second["title_cn"] = "论文二"
        second["abstract_en"] = "Abstract two with 2026 estimates."
        second["abstract_cn"] = "摘要二"
        return {
            "schema_version": "1.0",
            "issue_id": "jde-172-c",
            "journal_id": "jde",
            "journal_name": "Journal of Development Economics",
            "volume": "172",
            "issue": "C",
            "source_url": "https://www.sciencedirect.com/journal/journal-of-development-economics/vol/172/suppl/C",
            "retrieved_at": "2026-08-03T00:00:00+00:00",
            "expected_article_count": 2,
            "research_article_count": 2,
            "status": "incomplete",
            "articles": [article, second],
            "quality": {
                "roster_match": True,
                "order_preserved": True,
                "roster_transport": "crossref",
                "roster_authority": "crossref-provisional",
                "roster_match_scope": "crossref-issue-group",
                "publisher_page_status": "blocked",
                "excluded_item_count": 0,
                "excluded_items": [],
                "doi_complete": 2,
                "authors_complete": 2,
                "abstract_en_complete": 2,
                "translation_complete": 2,
                "duplicate_count": 0,
                "flags": [
                    "publisher_html_blocked_crossref_fallback",
                    "crossref_provisional_roster",
                ],
            },
        }

    def test_run_issue_archives_complete_elsevier_volume(self) -> None:
        import scripts.update_journals as update_journals_mod

        real_block = history_completeness_block
        issue = self._complete_elsevier_issue()
        ref = HistoricalIssue(
            "JDE",
            2025,
            "172",
            "c",
            issue["source_url"],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "collectors.metadata_fallback.fetch_crossref_current_issue",
                    return_value=issue,
                ),
                patch("scripts.backfill_history.STAGING_ROOT", root / "staging"),
                patch("scripts.backfill_history.STATE_PATH", root / "state.json"),
                patch("scripts.backfill_history.PUBLIC_API", root / "public"),
                patch(
                    "scripts.backfill_history.history_completeness_block",
                    side_effect=lambda iss, cfg: real_block(
                        iss, cfg, public_api=root / "public"
                    ),
                ),
                patch(
                    "scripts.backfill_history.apply_translation_cache",
                    side_effect=lambda iss: iss,
                ),
                patch(
                    "scripts.backfill_history.archive_issue",
                    side_effect=lambda item: update_journals_mod.archive_issue(
                        item, api_root=root / "public"
                    ),
                ),
            ):
                report = run_issue(
                    ref,
                    {
                        "id": "jde",
                        "name": "Journal of Development Economics",
                        "collector": "elsevier",
                        "issn": "0304-3878",
                    },
                    {},
                    translate=False,
                    max_translations=0,
                )
            self.assertEqual("complete", report["result"])
            archived = root / "public" / "journals" / "jde" / "issues" / "jde-172-c.json"
            self.assertTrue(archived.exists())
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("complete", state["issues"]["jde-172-c"]["status"])

    def test_run_issue_skips_blocked_thin_volume_without_recollect(self) -> None:
        ref = HistoricalIssue(
            "JDE",
            2025,
            "173",
            "c",
            "https://www.sciencedirect.com/journal/journal-of-development-economics/vol/173/suppl/C",
        )
        state = {
            "schema_version": "1.0",
            "issues": {
                "jde-173-c": {
                    "status": "blocked",
                    "last_error": (
                        "possible_incomplete_volume: 4 articles collected vs "
                        "current issue 34"
                    ),
                }
            },
        }
        with patch(
            "collectors.metadata_fallback.fetch_crossref_current_issue"
        ) as fetch:
            report = run_issue(
                ref,
                {"id": "jde", "collector": "elsevier"},
                state,
                translate=True,
                max_translations=10,
            )
        self.assertEqual("blocked", report["result"])
        self.assertIn("skipped", report["error"])
        fetch.assert_not_called()



    def test_backfill_notify_summarizes_state_and_builds_message(self) -> None:
        from scripts.backfill_notify import build_message, status_counts

        state = {
            "issues": {
                "jde-172-c": {"status": "complete"},
                "jde-175-c": {"status": "translation_partial"},
                "jde-173-c": {"status": "blocked"},
                "jde-174-c": {"status": ""},
            }
        }
        counts = status_counts(state)
        self.assertEqual(1, counts["complete"])
        self.assertEqual(1, counts["translation_partial"])
        self.assertEqual(1, counts["blocked"])
        self.assertEqual(1, counts["pending"])
        settings = SimpleNamespace(sender="a@example.com", recipients=("b@example.com",))
        message = build_message(
            counts,
            {"results": [{"issue_id": "jde-172-c", "result": "complete"}]},
            settings,
        )
        self.assertIn("完成 1", message["Subject"])
        self.assertIn("jde-172-c: complete", message.get_content())
        self.assertEqual("a@example.com", message["From"])
        self.assertEqual("b@example.com", message["To"])


if __name__ == "__main__":
    unittest.main()
