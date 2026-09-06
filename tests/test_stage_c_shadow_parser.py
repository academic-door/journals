from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.translate_issue import resolve_semantic_quantities


def _eq(source: str, translated: str) -> bool:
    source_q, translated_q = resolve_semantic_quantities(source, translated)
    return source_q == translated_q


# Real-corpus regression fixtures (resolved by Stage C-P2 semantic hardening).
POSITIVE = [
    ("duration-of-exemption", "two decades of sustained growth", "二十年的持续增长"),
    ("duration-of-exemption", "over six decades of global data", "全球数据的60年"),
    ("method-descriptor", "a two-quantile-regression approach", "双重分位数回归方法"),
    ("method-descriptor", "a one-step method", "单步方法"),
    ("currency-abbrev-mil", "approximately EUR 200 mil. to national GDP", "约200百万欧元"),
    ("currency-abbrev-M", "this translates to $18.5 M for each 1% improvement", "这相当于每改善1%带来$18.5百万"),
    ("fraction", "more than half a billion euros", "超过五亿欧元"),
    ("month-normalization", "December 2025", "2025年十二月"),
    ("month-normalization", "during April-December 2020", "在2020年四月至十二月期间"),
    ("written-cardinal-U2010", "Ninety-eight percent of beneficiaries repay the loan", "百分之九十八的受益人偿还贷款"),
    ("fold-multiplier", "price has fallen roughly a thousandfold", "价格已经下降了大约一千倍"),
    ("shared-scale-range", "ranged from 585.9 to 598.4 billion CNY", "旅游价值在5859亿至5984亿元人民币之间"),
    ("single-numeral-classifier", "optimal combinations of three patent instruments", "研究三种专利工具的最优组合"),
    ("single-numeral-classifier", "a one-child household", "假设 一 儿童家庭的当前门槛是正确的"),
    ("single-numeral-classifier", "spill over to one sector", "基于一个部门的制度"),
    ("single-numeral-classifier", "households with three and four children", "有 三 和 四 儿童的家庭"),
    ("reference-count", "the joint distribution of the two", "估计值与状态的联合分布"),
    ("reference-count", "Guided by three theories (human capital, rat race, tournament)", "根据人力资本、竞争和锦标赛理论"),
    ("reference-count", "Using two different indices, one remotely sensed and one from ground data", "利用包括遥感的一和地面观测的一的不同指数"),
    ("pp-unit-drop", "associated with a 0.7\u20131 percentage point increase", "与提高0.7-1相关"),
]

# Fail-closed negatives: true scale/value errors must STILL be detected.
NEGATIVE = [
    ("scale-error", "40 million", "40万"),
    ("scale-error", "13 million", "130万"),
    ("duration-wrong", "six decades", "50年"),
    ("currency-scale", "$1.0 million", "1.0万美元"),
    ("unit-mismatch", "3 years", "两个月"),
    ("count-wrong", "5 groups", "三组"),
    ("percent-wrong", "5 percent", "6%"),
    ("wrong-senses-count", "five external senses", "六种感官"),
    ("wrong-groupings", "four distinct groupings", "六个不同的群体"),
    ("year-date-wrong", "in 2001 and 2020", "在2001和2019之间"),
    ("reference-count-wrong", "by three theories", "根据两个理论"),
    ("pp-unit-wrong", "a 0.7\u20131 percentage point increase", "与提高0.7-2相关"),
]


def test_stage_c_positive_regressions():
    for family, source, translated in POSITIVE:
        assert _eq(source, translated), f"[{family}] {source!r} != {translated!r}"


def test_stage_c_fail_closed_negatives():
    for family, source, translated in NEGATIVE:
        assert not _eq(source, translated), (
            f"[{family}] fail-closed broken: {source!r} == {translated!r}"
        )
