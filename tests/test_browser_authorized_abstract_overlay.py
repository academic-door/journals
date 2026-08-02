from __future__ import annotations

import copy
import unittest

from scripts.translate_issue import _normalize_written_number_translations
from scripts.import_browser_authorized_abstract_overlay import (
    apply_overlay,
    clean_official_abstract,
    exclude_official_item_without_abstract,
    validate_overlay,
)


def base_issue() -> dict:
    return {
        "schema_version": "1.0",
        "issue_id": "sample-1-c",
        "journal_id": "sample",
        "journal_name": "Sample",
        "volume": "1",
        "issue": "C",
        "issue_label": "Vol. 1",
        "publication_date": "2026-08-01",
        "source_url": "https://www.sciencedirect.com/journal/sample/vol/1/suppl/C",
        "retrieved_at": "2026-08-02T00:00:00+00:00",
        "expected_article_count": 1,
        "research_article_count": 1,
        "status": "incomplete",
        "publication_state": "enriching",
        "development_sample": False,
        "articles": [{
            "paper_id": "doi:10.1016/j.sample.2026.1",
            "sequence": 1,
            "source_sequence": 1,
            "article_type": "research_article",
            "title_en": "A sample title",
            "title_cn": "",
            "authors": ["A. Author"],
            "abstract_en": "",
            "abstract_cn": "",
            "doi": "10.1016/j.sample.2026.1",
            "source_url": "https://www.sciencedirect.com/science/article/pii/S0123456789000001",
            "publication_date": "2026-08-01",
            "sources": {"issue": "official", "roster": "official", "metadata": "official"},
            "translation": {"status": "missing"},
            "quality_flags": ["title_cn_missing", "abstract_en_missing", "abstract_cn_missing"],
        }],
        "quality": {
            "roster_match": True,
            "order_preserved": True,
            "roster_authority": "official-issue-page",
            "roster_transport": "rss",
            "official_item_count": 1,
            "excluded_items": [],
            "doi_complete": 1,
            "authors_complete": 1,
            "abstract_en_complete": 0,
            "translation_complete": 0,
            "duplicate_count": 0,
            "flags": ["translation_incomplete"],
        },
    }


def overlay() -> dict:
    return {
        "schema_version": "1.0",
        "capture_mode": "browser-authorized-abstract-overlay",
        "journal_id": "sample",
        "captured_at": "2026-08-02T00:00:00Z",
        "privacy": {"cookies_stored": False, "credentials_stored": False, "session_data_stored": False},
        "items": [{
            "doi": "10.1016/j.sample.2026.1",
            "title_en": "A sample title",
            "source_url": "https://www.sciencedirect.com/science/article/pii/S0123456789000001",
            "abstract_en": "This is the complete official abstract.",
            "institutional_access_confirmed": True,
        }],
    }


class OverlayTests(unittest.TestCase):
    def test_mathml_duplicate_is_removed_from_official_abstract(self) -> None:
        value = 'min{k+1,n}(n-1)<math><mi>min</mi><mo>{</mo><mi>k</mi><mo>+</mo><mn>1</mn><mo>,</mo><mi>n</mi><mo>}</mo><mo>(</mo><mi>n</mi><mo>-</mo><mn>1</mn><mo>)</mo></math> items'
        self.assertEqual(clean_official_abstract(value), "min{k+1,n}(n-1) items")

    def test_mathml_formula_is_kept_when_not_already_rendered(self) -> None:
        value = 'process <math><msup><mi>S</mi><mi>∞</mi></msup><mi>W</mi><mo>*</mo></math> characterizes it'
        self.assertEqual(clean_official_abstract(value), "process S∞W* characterizes it")

    def test_written_number_normalizer_preserves_multiplication_notation(self) -> None:
        source = "two payoff types of players in 2 × 2 games"
        translated = "两种收益类型参与者参与 2×2 博弈"
        self.assertEqual(
            _normalize_written_number_translations(source, translated),
            translated,
        )

    def test_applies_official_abstract_without_private_state(self) -> None:
        issue = apply_overlay(base_issue(), overlay())
        self.assertEqual(issue["articles"][0]["abstract_en"], "This is the complete official abstract.")
        self.assertEqual(issue["quality"]["abstract_en_complete"], 1)
        self.assertFalse(issue["quality"]["browser_authorized_abstracts"]["privacy_fields_stored"])

    def test_rejects_cookie_shaped_fields(self) -> None:
        value = overlay()
        value["cookie"] = "secret"
        with self.assertRaisesRegex(ValueError, "forbidden private field"):
            validate_overlay(value)

    def test_rejects_title_mismatch(self) -> None:
        value = overlay()
        value["items"][0]["title_en"] = "Different title"
        with self.assertRaisesRegex(ValueError, "title does not match"):
            apply_overlay(base_issue(), value)

    def test_explicit_no_abstract_exclusion_is_auditable(self) -> None:
        issue = base_issue()
        issue["articles"][0]["article_type"] = "short_communication"
        result = exclude_official_item_without_abstract(
            copy.deepcopy(issue),
            doi="10.1016/j.sample.2026.1",
            title_en="A sample title",
            source_url="https://www.sciencedirect.com/science/article/pii/S0123456789000001",
            raw_type="Short communication",
            reason="official_abstract_not_provided_short_communication",
        )
        self.assertEqual(result["research_article_count"], 0)
        self.assertEqual(result["quality"]["excluded_items"][0]["reason"], "official_abstract_not_provided_short_communication")


if __name__ == "__main__":
    unittest.main()
