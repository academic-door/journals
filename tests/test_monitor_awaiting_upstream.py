"""Regression tests for _is_awaiting_upstream (#179 marker refinement).

A genuine upstream/source or enrichment gap must remain awaiting-upstream, but a
real operational failure must NOT be swallowed merely because the error text
mentions a route-descriptor marker.
"""

from __future__ import annotations

import unittest

from scripts.journal_monitor import _is_awaiting_upstream


class AwaitingUpstreamClassificationTests(unittest.TestCase):
    def test_legitimate_source_lag_is_awaiting(self) -> None:
        self.assertTrue(_is_awaiting_upstream("SourceLagError: next issue not yet published"))

    def test_legitimate_abstract_incomplete_is_awaiting(self) -> None:
        self.assertTrue(_is_awaiting_upstream("abstract_en_incomplete for 3 articles"))

    def test_legitimate_missing_abstracts_is_awaiting(self) -> None:
        self.assertTrue(_is_awaiting_upstream("missing abstracts for 2 items"))

    def test_legitimate_authority_gap_is_awaiting(self) -> None:
        self.assertTrue(_is_awaiting_upstream("source authority is not publication-ready"))

    def test_legitimate_provisional_roster_is_awaiting(self) -> None:
        self.assertTrue(
            _is_awaiting_upstream("provisional Crossref roster requires official confirmation")
        )

    def test_empty_error_is_not_awaiting(self) -> None:
        self.assertFalse(_is_awaiting_upstream(""))

    def test_publisher_rss_reverse_order_marker_is_a_failure(self) -> None:
        # The route descriptor alone must not suppress a genuine operational failure.
        self.assertFalse(
            _is_awaiting_upstream(
                "RAND: publisher_rss_reverse_order_normalized failed to fetch feed (HTTP 503)"
            )
        )

    def test_publisher_html_blocked_repec_marker_is_a_failure(self) -> None:
        self.assertFalse(
            _is_awaiting_upstream(
                "publisher_html_blocked_repec_fallback Error: connection refused"
            )
        )

    def test_generic_operational_failure_is_a_failure(self) -> None:
        self.assertFalse(_is_awaiting_upstream("connection timeout to wiley feed"))


if __name__ == "__main__":
    unittest.main()
