import unittest

from scripts.preflight_source_wave import validate_wave


class SourceWavePreflightTests(unittest.TestCase):
    def test_requires_exact_issue_set_and_preserves_metadata(self) -> None:
        result = validate_wave(
            {"records": [
                {"issue_id": "a-1", "category": "recoverable"},
                {"issue_id": "a-2", "category": "source_pending"},
            ]},
            issue_ids=["a-1", "a-2"],
            categories=["recoverable", "source_pending"],
            publisher="Example",
            collector="wiley",
            strategy_status="unvalidated",
        )
        self.assertEqual(["a-1", "a-2"], result["matched_issue_ids"])
        self.assertTrue(result["hard_gate"]["sets_equal"])
        self.assertEqual("wiley", result["collector_strategy"])

    def test_rejects_category_or_issue_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "preflight failed"):
            validate_wave(
                {"records": [{"issue_id": "a-1", "category": "recoverable"}]},
                issue_ids=["a-1", "a-2"],
                categories=["recoverable"],
            )


if __name__ == "__main__":
    unittest.main()
