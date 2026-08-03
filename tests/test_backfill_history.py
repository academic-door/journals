from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.history import HistoricalIssue
from scripts.backfill_history import (
    atomic_write_json,
    collector_for_issue,
    history_completeness_block,
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


if __name__ == "__main__":
    unittest.main()
