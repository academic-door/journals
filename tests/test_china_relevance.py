from __future__ import annotations

import unittest

from scripts.china_relevance import classify_china_relevance
from scripts.update_journals import clean_abstract_label, normalize_issue_content


class ChinaRelevanceTests(unittest.TestCase):
    def test_china_aligned_inputs_are_china_related(self) -> None:
        article = {
            "title_en": "Inputs in distress",
            "title_cn": "陷入困境的投入",
            "abstract_en": (
                "A reduction in FCI imports from China-aligned countries "
                "could reduce manufacturing value added."
            ),
            "abstract_cn": "",
        }
        result = classify_china_relevance(article)
        self.assertEqual("yes", result["status"])
        self.assertEqual("abstract", result["evidence"])
        self.assertIn("China-aligned", result["matched_terms"][0])

    def test_colombian_robot_study_is_not_china_related(self) -> None:
        article = {
            "title_en": (
                "US robot impacts in developing countries: "
                "Evidence from Colombian workers"
            ),
            "title_cn": "美国机器人对发展中国家的影响：来自哥伦比亚工人的证据",
            "abstract_en": (
                "This paper uses Colombian records and US robot adoption data."
            ),
            "abstract_cn": "本文使用哥伦比亚记录和美国机器人采用数据。",
        }
        self.assertEqual("no", classify_china_relevance(article)["status"])

    def test_title_signal_is_traceable(self) -> None:
        result = classify_china_relevance(
            {
                "title_en": "Trade policy in China",
                "title_cn": "",
                "abstract_en": "",
                "abstract_cn": "",
            }
        )
        self.assertEqual("yes", result["status"])
        self.assertEqual("title", result["evidence"])
        self.assertEqual("academic-door-rules-v1", result["source"])

    def test_publisher_abstract_labels_are_removed(self) -> None:
        self.assertEqual(
            "The paper studies trade.",
            clean_abstract_label("Abstract The paper studies trade."),
        )
        self.assertEqual(
            "本文研究贸易。",
            clean_abstract_label("摘要 本文研究贸易。"),
        )

    def test_existing_issue_is_normalized_without_recollection(self) -> None:
        issue = {
            "articles": [
                {
                    "title_en": "Inputs in distress",
                    "title_cn": "陷入困境的投入",
                    "abstract_en": "Abstract Imports from China affect production.",
                    "abstract_cn": "摘要 来自中国的进口影响生产。",
                }
            ]
        }
        normalized = normalize_issue_content(issue)
        article = normalized["articles"][0]
        self.assertEqual(
            "Imports from China affect production.", article["abstract_en"]
        )
        self.assertEqual("来自中国的进口影响生产。", article["abstract_cn"])
        self.assertEqual("yes", article["china_relevance"]["status"])


if __name__ == "__main__":
    unittest.main()
