from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

from collectors.history import HistoricalIssue
from scripts.backfill_history import (
    COLLECTOR_REVISION,
    atomic_write_json,
    collector_for_issue,
    history_completeness_block,
    is_actionable,
    migrate_legacy_state,
    main as backfill_main,
    plan_from_discovery,
    retry_class_for,
    rotate_journals,
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

    def test_retry_class_separates_manual_and_transient_failures(self) -> None:
        self.assertEqual("translation", retry_class_for("translation_partial", ""))
        self.assertEqual("manual", retry_class_for("blocked", "possible_incomplete_volume"))
        self.assertEqual("transient", retry_class_for("blocked", "HTTP 503 timeout"))
        self.assertEqual(
            "missing_abstract",
            retry_class_for("blocked", "CrossrefRosterError: abstract missing"),
        )
        self.assertEqual("in_progress", retry_class_for("blocked", "publisher not finalised"))
        self.assertEqual("manual", retry_class_for("blocked", "UnexpectedError"))

    def test_is_actionable_skips_manual_and_future_retries(self) -> None:
        self.assertTrue(is_actionable(None))
        self.assertFalse(is_actionable({"status": "complete"}))
        self.assertFalse(
            is_actionable(
                {"status": "blocked", "retry_class": "manual", "last_error": ""}
            )
        )
        self.assertFalse(
            is_actionable(
                {
                    "status": "blocked",
                    "retry_class": "transient",
                    "next_retry_at": "2099-01-01T00:00:00+00:00",
                }
            )
        )
        self.assertTrue(
            is_actionable(
                {"status": "blocked", "retry_class": "transient", "next_retry_at": ""}
            )
        )

    def test_rotate_journals_rotates_by_last_run_and_respects_cap(self) -> None:
        state = {
            "issues": {
                "aer-1-1": {"journal": "AER", "year": 2023, "status": "ready"},
                "jde-1-1": {
                    "journal": "JDE",
                    "year": 2023,
                    "status": "blocked",
                    "retry_class": "transient",
                },
                "jpe-1-1": {
                    "journal": "JPE",
                    "year": 2023,
                    "status": "blocked",
                    "retry_class": "manual",
                },
            },
            "rotation": {
                "last_run_at": {
                    "AER": "2026-08-09T00:00:00+00:00",
                    "JDE": "2026-08-08T00:00:00+00:00",
                }
            },
            "discovery": {
                "AER": {
                    "issue_ids": ["aer-1-1"],
                    "issue_years": {"aer-1-1": 2023},
                    "refreshed_at": "2026-08-10T00:00:00+00:00",
                    "collector_revision": COLLECTOR_REVISION,
                },
                "JDE": {
                    "issue_ids": ["jde-1-1"],
                    "issue_years": {"jde-1-1": 2023},
                    "refreshed_at": "2026-08-10T00:00:00+00:00",
                    "collector_revision": COLLECTOR_REVISION,
                },
                "JPE": {
                    "issue_ids": ["jpe-1-1"],
                    "issue_years": {"jpe-1-1": 2023},
                    "refreshed_at": "2026-08-10T00:00:00+00:00",
                    "collector_revision": COLLECTOR_REVISION,
                },
            },
        }
        history = {"journals": {"AER": {}, "JDE": {}, "JPE": {}, "QE": {}}}
        chosen = rotate_journals(
            state,
            history,
            years=range(2023, 2025),
            max_journals=2,
            now=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )
        # AER is truly ready in a fresh discovery snapshot; JPE is manual.
        # JDE is retryable and QE has no discovery snapshot, so both rotate.
        self.assertEqual(["QE", "JDE"], chosen)

    def test_rotate_detects_unregistered_issue_in_fresh_snapshot(self) -> None:
        state = {
            "schema_version": "1.1",
            "issues": {
                "aer-114-1": {"journal": "AER", "year": 2024, "status": "ready"}
            },
            "discovery": {
                "AER": {
                    "issue_ids": ["aer-114-1", "aer-114-2", "aer-114-10"],
                    "issue_years": {
                        "aer-114-1": 2024,
                        "aer-114-2": 2024,
                        "aer-114-10": 2024,
                    },
                    "refreshed_at": "2026-08-10T00:00:00+00:00",
                    "collector_revision": COLLECTOR_REVISION,
                }
            },
        }
        chosen = rotate_journals(
            state,
            {"journals": {"AER": {}}},
            years=range(2024, 2025),
            max_journals=1,
            now=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )
        self.assertEqual(["AER"], chosen)

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
        with (
            patch(
                "collectors.wiley.fetch_current_issue",
                side_effect=RuntimeError("publisher blocked"),
            ) as official,
            patch(
                "collectors.metadata_fallback.fetch_repec_history_issue",
                return_value={"issue_id": "ecta-93-1"},
            ) as fetch,
        ):
            result = collector_for_issue(config, issue)()

        self.assertEqual("ecta-93-1", result["issue_id"])
        official.assert_called_once()
        fetch.assert_called_once_with(
            journal_id="ecta",
            journal_name="Econometrica",
            issn="0012-9682",
            volume="93",
            issue="1",
            repec_series_code="wly/emetrp",
        )

    def test_wiley_official_collector_precedes_fallback_with_real_config(self) -> None:
        import yaml

        journals = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config/journals.yml").read_text(
                encoding="utf-8"
            )
        )["journals"]
        config = journals["ECTA"]
        issue = HistoricalIssue(
            "ECTA",
            2025,
            "93",
            "1",
            "https://onlinelibrary.wiley.com/toc/14680262/2025/93/1",
        )
        official_payload = {"issue_id": "ecta-93-1", "volume": "93", "issue": "1"}
        with (
            patch(
                "collectors.wiley.fetch_current_issue", return_value=official_payload
            ) as official,
            patch("collectors.metadata_fallback.fetch_repec_history_issue") as fallback,
        ):
            result = collector_for_issue(config, issue)()
        self.assertIs(official_payload, result)
        official.assert_called_once()
        fallback.assert_not_called()

    def test_jpe_fallback_order_is_official_then_repec_then_crossref(self) -> None:
        import yaml

        config = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config/journals.yml").read_text(
                encoding="utf-8"
            )
        )["journals"]["JPE"]
        issue = HistoricalIssue(
            "JPE",
            2024,
            "132",
            "4",
            "https://www.journals.uchicago.edu/toc/jpe/2024/132/4",
        )
        crossref_payload = {
            "issue_id": "jpe-132-4",
            "volume": "132",
            "issue": "4",
            "quality": {"flags": []},
        }
        calls: list[str] = []

        def official(*args, **kwargs):
            calls.append("official")
            raise RuntimeError("blocked")

        def repec(*args, **kwargs):
            calls.append("repec")
            raise RuntimeError("missing")

        def crossref(*args, **kwargs):
            calls.append("crossref")
            return crossref_payload

        with (
            patch("collectors.chicago.fetch_current_issue", side_effect=official),
            patch(
                "collectors.metadata_fallback.fetch_repec_history_issue",
                side_effect=repec,
            ),
            patch(
                "collectors.metadata_fallback.fetch_crossref_current_issue",
                side_effect=crossref,
            ),
        ):
            result = collector_for_issue(config, issue)()
        self.assertEqual(["official", "repec", "crossref"], calls)
        self.assertEqual("source_pending", result["source_status"])
        self.assertIn(
            "crossref_provisional_roster", result["quality"]["flags"]
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
        with (
            patch(
                "collectors.elsevier.fetch_elsevier_repec_history_issue",
                side_effect=RuntimeError("repec unavailable"),
            ),
            patch(
                "collectors.metadata_fallback.fetch_elsevier_issue_via_search",
                side_effect=RuntimeError("search unavailable"),
            ),
            patch(
                "collectors.metadata_fallback.fetch_crossref_current_issue",
                return_value={"issue_id": "jde-172-c"},
            ) as fetch,
        ):
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

    def test_run_issue_keeps_crossref_volume_source_pending(self) -> None:
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
                    "collectors.elsevier.fetch_elsevier_repec_history_issue",
                    side_effect=RuntimeError("repec unavailable"),
                ),
                patch(
                    "collectors.metadata_fallback.fetch_elsevier_issue_via_search",
                    side_effect=RuntimeError("search unavailable"),
                ),
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
                    side_effect=lambda item, **kwargs: update_journals_mod.archive_issue(
                        item, api_root=root / "public", **kwargs
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
            self.assertEqual("source_pending", report["result"])
            archived = root / "public" / "journals" / "jde" / "issues" / "jde-172-c.json"
            self.assertTrue(archived.exists())
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("1.1", state["schema_version"])
            self.assertEqual(
                "source_pending", state["issues"]["jde-172-c"]["status"]
            )
            archived_payload = json.loads(archived.read_text(encoding="utf-8"))
            self.assertEqual("complete", archived_payload["content_status"])
            self.assertEqual("source_pending", archived_payload["source_status"])
            self.assertEqual("source_pending", archived_payload["publication_state"])

    def test_legacy_complete_state_is_reconciled_against_archive_truth(self) -> None:
        issue = self._complete_elsevier_issue()
        verified = json.loads(json.dumps(issue))
        verified["issue_id"] = "aer-114-1"
        verified["journal_id"] = "aer"
        verified["source_url"] = "https://www.aeaweb.org/issues/700"
        verified["quality"]["flags"] = []
        verified["quality"]["roster_authority"] = "official-issue-page"
        verified["quality"]["roster_transport"] = "official-issue-page"
        partial = json.loads(json.dumps(verified))
        partial["issue_id"] = "aer-114-2"
        partial["articles"][0]["abstract_cn"] = ""

        with tempfile.TemporaryDirectory() as directory:
            api_root = Path(directory) / "api"
            issue_dir = api_root / "journals" / "aer" / "issues"
            issue_dir.mkdir(parents=True)
            (issue_dir / "aer-114-1.json").write_text(
                json.dumps(verified), encoding="utf-8"
            )
            (issue_dir / "aer-114-2.json").write_text(
                json.dumps(partial), encoding="utf-8"
            )
            state = {
                "schema_version": "1.0",
                "issues": {
                    "aer-114-1": {"journal": "AER", "status": "complete"},
                    "aer-114-2": {"journal": "AER", "status": "complete"},
                    "aer-114-3": {"journal": "AER", "status": "complete"},
                },
            }
            changed = migrate_legacy_state(
                state, {"AER": {"id": "aer"}}, public_api=api_root
            )
            ready_archive = json.loads(
                (issue_dir / "aer-114-1.json").read_text(encoding="utf-8")
            )
        self.assertTrue(changed)
        self.assertEqual("1.1", state["schema_version"])
        self.assertEqual("ready", state["issues"]["aer-114-1"]["status"])
        self.assertEqual(
            "translation_partial", state["issues"]["aer-114-2"]["status"]
        )
        self.assertEqual("blocked", state["issues"]["aer-114-3"]["status"])
        self.assertEqual("ready", ready_archive["publication_state"])

    def test_source_pending_archive_upgrades_to_official_ready(self) -> None:
        import scripts.update_journals as update_journals_mod

        provisional = self._complete_elsevier_issue()
        provisional.update(
            issue_id="aer-114-1",
            journal_id="aer",
            journal_name="American Economic Review",
            volume="114",
            issue="1",
            source_url="https://www.aeaweb.org/issues/700",
        )
        verified = json.loads(json.dumps(provisional))
        verified["quality"]["flags"] = []
        verified["quality"]["roster_authority"] = "official-issue-page"
        verified["quality"]["roster_transport"] = "official-issue-page"
        ref = HistoricalIssue(
            "AER", 2024, "114", "1", "https://www.aeaweb.org/issues/700"
        )
        state = {
            "schema_version": "1.1",
            "issues": {
                "aer-114-1": {
                    "journal": "AER",
                    "year": 2024,
                    "volume": "114",
                    "issue": "1",
                    "status": "source_pending",
                    "retry_class": "source",
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update_journals_mod.archive_issue(provisional, api_root=root / "public")
            with (
                patch(
                    "collectors.aea.fetch_current_issue", return_value=verified
                ) as official,
                patch("scripts.backfill_history.STAGING_ROOT", root / "staging"),
                patch("scripts.backfill_history.STATE_PATH", root / "state.json"),
                patch("scripts.backfill_history.PUBLIC_API", root / "public"),
                patch(
                    "scripts.backfill_history.apply_translation_cache",
                    side_effect=lambda item: item,
                ),
                patch(
                    "scripts.backfill_history.history_completeness_block",
                    return_value="",
                ),
                patch(
                    "scripts.backfill_history.archive_issue",
                    side_effect=lambda item, **kwargs: update_journals_mod.archive_issue(
                        item, api_root=root / "public", **kwargs
                    ),
                ),
            ):
                report = run_issue(
                    ref,
                    {
                        "id": "aer",
                        "name": "American Economic Review",
                        "collector": "aea",
                        "fallback": "crossref",
                        "issn": "0002-8282",
                    },
                    state,
                    translate=False,
                    max_translations=0,
                )
            archived = json.loads(
                (
                    root
                    / "public"
                    / "journals"
                    / "aer"
                    / "issues"
                    / "aer-114-1.json"
                ).read_text(encoding="utf-8")
            )
        official.assert_called_once()
        self.assertEqual("ready", report["result"], report)
        self.assertEqual("official_verified", archived["source_status"])
        self.assertEqual("ready", archived["publication_state"])

    def test_plan_uses_persisted_discovery_without_refresh(self) -> None:
        state = {
            "discovery": {
                "AER": {
                    "issue_ids": ["aer-114-1", "aer-114-10", "aer-114-2"],
                    "issue_refs": {
                        issue_id: {
                            "journal": "AER",
                            "year": 2024,
                            "volume": "114",
                            "issue": issue_id.rsplit("-", 1)[-1],
                            "official_url": f"https://www.aeaweb.org/issues/{issue_id}",
                        }
                        for issue_id in (
                            "aer-114-1",
                            "aer-114-10",
                            "aer-114-2",
                        )
                    },
                }
            }
        }
        plan = plan_from_discovery(state, ["AER"], range(2024, 2025))
        self.assertEqual(
            ["aer-114-1", "aer-114-2", "aer-114-10"],
            [item.issue_id for item in plan],
        )

    def test_refresh_discovery_only_persists_snapshot_without_collection(self) -> None:
        import scripts.backfill_history as backfill_module

        discovered = [
            HistoricalIssue(
                "AER", 2025, "115", "1", "https://www.aeaweb.org/issues/800"
            ),
            HistoricalIssue(
                "AER", 2025, "115", "2", "https://www.aeaweb.org/issues/801"
            ),
        ]
        old_state_path = backfill_module.STATE_PATH
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "field-2025-2026.json"
            argv = [
                "backfill_history.py",
                "--config",
                str(Path(__file__).resolve().parents[1] / "config/top5-history.yml"),
                "--state",
                str(state_path),
                "--journal",
                "AER",
                "--from-year",
                "2025",
                "--to-year",
                "2026",
                "--refresh-discovery-only",
            ]
            try:
                with (
                    patch("sys.argv", argv),
                    patch(
                        "scripts.backfill_history.discover_official_issues",
                        return_value=discovered,
                    ) as discover,
                    patch("scripts.backfill_history.run_issue") as collect,
                    redirect_stdout(io.StringIO()),
                ):
                    result = backfill_main()
                state = json.loads(state_path.read_text(encoding="utf-8"))
            finally:
                backfill_module.STATE_PATH = old_state_path
        self.assertEqual(0, result)
        discover.assert_called_once()
        collect.assert_not_called()
        self.assertEqual("1.1", state["schema_version"])
        self.assertEqual(
            ["aer-115-1", "aer-115-2"],
            state["discovery"]["AER"]["issue_ids"],
        )
        self.assertEqual({}, state["issues"])

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
                "jde-176-c": {"status": "ready"},
                "jde-175-c": {"status": "translation_partial"},
                "jde-173-c": {"status": "blocked"},
                "jde-174-c": {"status": ""},
            }
        }
        counts = status_counts(state)
        self.assertEqual(2, counts["complete"])
        self.assertEqual(1, counts["translation_partial"])
        self.assertEqual(1, counts["blocked"])
        self.assertEqual(1, counts["pending"])
        settings = SimpleNamespace(sender="a@example.com", recipients=("b@example.com",))
        message = build_message(
            counts,
            {"results": [{"issue_id": "jde-172-c", "result": "complete"}]},
            settings,
        )
        self.assertIn("完成 2", message["Subject"])
        self.assertIn("jde-172-c: complete", message.get_content())
        self.assertEqual("a@example.com", message["From"])
        self.assertEqual("b@example.com", message["To"])



class ElsevierSearchRosterFilterTests(unittest.TestCase):
    """ScienceDirect Search API results must be scoped to the journal's ISSN.

    The API can return articles from other Elsevier journals that happen to
    share the same volume number; only entries whose PII embeds this
    journal's ISSN belong in the issue roster.
    """

    def test_foreign_journal_entries_are_dropped(self) -> None:
        import xml.etree.ElementTree as ET

        from collectors import metadata_fallback

        issn = "0167-2681"  # Journal of Economic Behavior & Organization
        expected_prefix = "S01672681"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:dc="http://purl.org/dc/elements/1.1/"
              xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
              xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
              xmlns:sci="http://www.elsevier.com/xml/schemas/sciencedirect">
          <opensearch:totalResults>2</opensearch:totalResults>
          <entry>
            <dc:title>Real JEBO article</dc:title>
            <prism:doi>10.1016/j.jebo.2026.100001</prism:doi>
            <sci:pii>{expected_prefix}202600001</sci:pii>
            <prism:coverDate>2026-02-01</prism:coverDate>
            <dc:creator>Alice</dc:creator>
          </entry>
          <entry>
            <dc:title>Wrong journal article</dc:title>
            <prism:doi>10.1016/j.semcdb.2026.100002</prism:doi>
            <sci:pii>S1044577X26000022</sci:pii>
            <prism:coverDate>2026-02-01</prism:coverDate>
            <dc:creator>Bob</dc:creator>
          </entry>
        </feed>"""

        class FakeResponse:
            content = xml.encode("utf-8")

            def raise_for_status(self) -> None:
                return None

        class FakeSession:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return FakeResponse()

        session = FakeSession()
        with (
            patch.dict("os.environ", {"ELSEVIER_API_KEY": "test-key"}, clear=False),
            patch.object(
                metadata_fallback, "_elsevier_lookup", return_value={}
            ),
        ):
            issue = metadata_fallback.fetch_elsevier_issue_via_search(
                journal_id="jebo",
                journal_name="Journal of Economic Behavior & Organization",
                issn=issn,
                volume="242",
                session=session,
            )
        articles = issue["articles"]
        self.assertEqual(1, len(articles))
        self.assertEqual("10.1016/j.jebo.2026.100001", articles[0]["doi"])
        self.assertEqual("elsevier-search-api", issue["quality"]["roster_authority"])
        self.assertEqual("10.1016/j.jebo.2026.100001", articles[0]["doi"])

    def test_no_matching_pii_raises(self) -> None:
        import xml.etree.ElementTree as ET

        from collectors import metadata_fallback

        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:dc="http://purl.org/dc/elements/1.1/"
              xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
              xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
              xmlns:sci="http://www.elsevier.com/xml/schemas/sciencedirect">
          <opensearch:totalResults>1</opensearch:totalResults>
          <entry>
            <dc:title>Only foreign article</dc:title>
            <prism:doi>10.1016/j.other.2026.1</prism:doi>
            <sci:pii>S9999999920260001</sci:pii>
          </entry>
        </feed>"""

        class FakeResponse:
            content = xml.encode("utf-8")

            def raise_for_status(self) -> None:
                return None

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        with (
            patch.dict("os.environ", {"ELSEVIER_API_KEY": "test-key"}, clear=False),
            self.assertRaises(metadata_fallback.MetadataFallbackError),
        ):
            metadata_fallback.fetch_elsevier_issue_via_search(
                journal_id="jebo",
                journal_name="Journal of Economic Behavior & Organization",
                issn="0167-2681",
                volume="242",
                session=FakeSession(),
            )





class MetadataEnrichmentTests(unittest.TestCase):
    def test_enrichment_fills_missing_abstract_and_authors(self) -> None:
        from collectors.metadata_fallback import enrich_missing_metadata

        issue = {
            "journal_id": "wd",
            "articles": [
                {
                    "doi": "10.1016/j.worlddev.2026.100001",
                    "title_en": "GVC sustainability",
                    "authors": [],
                    "abstract_en": "",
                    "sources": {"roster": "crossref"},
                },
                {
                    "doi": "10.1016/j.worlddev.2026.100002",
                    "title_en": "Mining in development",
                    "authors": ["A. Author"],
                    "abstract_en": "Already present.",
                    "sources": {"roster": "crossref"},
                },
            ],
        }
        # Fake responses: Semantic Scholar returns nothing, Elsevier returns
        # an abstract for the first DOI, OpenAlex returns nothing.
        def fake_get(*args, **kwargs):
            class R:
                status_code = 404
                content = b""
                headers = {}

                def raise_for_status(self):
                    return None

            return R()

        class FakeSession:
            def get(self, *args, **kwargs):
                return fake_get()

            def post(self, *args, **kwargs):
                class R:
                    status_code = 200

                    def raise_for_status(self):
                        return None

                    def json(self):
                        return [None]

                return R()

        with (
            mock.patch(
                "collectors.metadata_fallback._elsevier_lookup",
                return_value={
                    "abstract": "Filled by Elsevier.",
                    "source": "elsevier-api",
                },
            ),
            mock.patch(
                "collectors.metadata_fallback._openalex_metadata",
                return_value=([], "", ""),
            ),
        ):
            enriched = enrich_missing_metadata(
                issue, session=FakeSession(), timeout=5
            )
        self.assertEqual(
            "Filled by Elsevier.",
            enriched["articles"][0]["abstract_en"],
        )
        self.assertEqual(
            "elsevier-api",
            enriched["articles"][0]["sources"]["abstract_en"],
        )
        self.assertEqual(
            "Already present.",
            enriched["articles"][1]["abstract_en"],
        )

    def test_enrichment_is_noop_when_nothing_missing(self) -> None:
        from collectors.metadata_fallback import enrich_missing_metadata

        issue = {
            "journal_id": "x",
            "articles": [
                {
                    "doi": "10.1/x",
                    "title_en": "T",
                    "authors": ["A"],
                    "abstract_en": "Full.",
                }
            ],
        }
        result = enrich_missing_metadata(issue, session=None, timeout=5)
        self.assertIs(issue, result)





    def test_jina_reader_extracts_abstract(self) -> None:
        from collectors.metadata_fallback import _extract_jina_abstract

        page = """
        Title
        Some preamble text that should be ignored.

        Abstract
        This is the real abstract sentence one.
        It continues with sentence two.

        Keywords
        economics
        """
        self.assertEqual(
            "This is the real abstract sentence one. It continues with sentence two.",
            _extract_jina_abstract(page),
        )

    def test_enrichment_uses_jina_when_key_configured(self) -> None:
        from collectors.metadata_fallback import enrich_missing_metadata

        issue = {
            "journal_id": "jle",
            "articles": [
                {
                    "doi": "10.1086/736999",
                    "title_en": "Occupational licensing",
                    "authors": ["A. Author"],
                    "abstract_en": "",
                    "sources": {"roster": "crossref"},
                }
            ],
        }

        class FakeSession:
            def get(self, *args, **kwargs):
                class R:
                    status_code = 200

                    def raise_for_status(self):
                        return None

                    @property
                    def content(self):
                        return b"Abstract\nFilled by Jina Reader.\nKeywords\nx"

                return R()

            def post(self, *args, **kwargs):
                class R:
                    status_code = 200

                    def raise_for_status(self):
                        return None

                    def json(self):
                        return [None]

                return R()

        with (
            mock.patch.dict(
                "os.environ", {"JINA_API_KEY": "test-jina"}, clear=False
            ),
            mock.patch(
                "collectors.metadata_fallback._elsevier_lookup",
                return_value={"abstract": "", "source": ""},
            ),
            mock.patch(
                "collectors.metadata_fallback._openalex_metadata",
                return_value=([], "", ""),
            ),
        ):
            enriched = enrich_missing_metadata(
                issue, session=FakeSession(), timeout=5
            )
        self.assertEqual("Filled by Jina Reader.", enriched["articles"][0]["abstract_en"])
        self.assertEqual(
            "jina-reader",
            enriched["articles"][0]["sources"]["abstract_en"],
        )



if __name__ == "__main__":
    unittest.main()
