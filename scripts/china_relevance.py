from __future__ import annotations

import re
from typing import Any


TITLE_PATTERN = re.compile(
    r"\bChina(?:['’]s|ese)?\b|\bChinese\b|(?<!发展)中国|大陆|北京|上海|香港|台湾",
    re.IGNORECASE,
)
ABSTRACT_PATTERNS = (
    re.compile(
        r"\bChina(?:['’]s|-aligned|-based|-related|-focused|-specific)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:in|from|of|for|among|across|within|into|to)\s+"
        r"(?:mainland\s+)?China\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bChinese\s+(?:economy|market|firms?|households?|cities|workers?|"
        r"students?|manufacturers?|exporters?|provinces?|consumers?|banks?|"
        r"government|policy|trade|imports?|exports?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:中国|大陆|北京|上海|香港|台湾)(?:经济|市场|企业|家庭|城市|农村|"
        r"制造|出口|进口|省份|劳动力|学生|消费者|银行|政府|政策|贸易)",
        re.IGNORECASE,
    ),
)


def classify_china_relevance(article: dict[str, Any]) -> dict[str, Any]:
    """Classify whether China is a substantive study setting or mechanism."""

    title = f"{article.get('title_en', '')} {article.get('title_cn', '')}".strip()
    abstract = (
        f"{article.get('abstract_en', '')} {article.get('abstract_cn', '')}".strip()
    )
    title_match = TITLE_PATTERN.search(title)
    if title_match:
        return {
            "status": "yes",
            "evidence": "title",
            "matched_terms": [title_match.group(0)],
            "source": "academic-door-rules-v1",
        }
    for pattern in ABSTRACT_PATTERNS:
        abstract_match = pattern.search(abstract)
        if abstract_match:
            return {
                "status": "yes",
                "evidence": "abstract",
                "matched_terms": [abstract_match.group(0)],
                "source": "academic-door-rules-v1",
            }
    return {
        "status": "no",
        "evidence": "no substantive China signal",
        "matched_terms": [],
        "source": "academic-door-rules-v1",
    }


def annotate_issue(issue: dict[str, Any]) -> dict[str, Any]:
    for article in issue.get("articles", []):
        article["china_relevance"] = classify_china_relevance(article)
    return issue
