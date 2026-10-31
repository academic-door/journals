"""One-time, exact-identity IJIO 108(C) publisher-period correction.

Official issue month is attested independently by Elsevier article pages
https://www.sciencedirect.com/science/article/pii/S0167718726000354
https://www.sciencedirect.com/science/article/pii/S0167718726000639
Both identify International Journal of Industrial Organization Volume 108
as October 2026. Only the pre-seeded, rights-withheld, publisher-supplied
metadata issue is eligible. This script has NO Git or network access.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

ISSUE = "ijio-108-c"
OLD_PERIOD = "2026"
OFFICIAL_PERIOD = "October 2026"
DOIS = [
    "10.1016/j.ijindorg.2026.103282",
    "10.1016/j.ijindorg.2026.103283",
    "10.1016/j.ijindorg.2026.103291",
    "10.1016/j.ijindorg.2026.103304",
    "10.1016/j.ijindorg.2026.103310",
    "10.1016/j.ijindorg.2026.103311",
]


def eligible(issue: dict) -> bool:
    quality = issue.get("quality", {})
    rights = issue.get("public_content", {})
    articles = issue.get("articles", [])
    return (
        issue.get("journal_id") == "ijio"
        and issue.get("issue_id") == ISSUE
        and issue.get("source_url") == "https://ideas.repec.org/s/eee/indorg.html"
        and issue.get("source_status") == "publisher_verified"
        and issue.get("publication_state") == "ready"
        and issue.get("public_mode") == "metadata_first_rights_qualified_v1"
        and issue.get("content_status") == "metadata_complete"
        and quality.get("roster_authority") == "repec-publisher-supplied"
        and quality.get("roster_match") is True
        and quality.get("order_preserved") is True
        and not quality.get("excluded_items", [])
        and rights.get("metadata_articles") == 6
        and rights.get("rights_withheld_articles") == 6
        and rights.get("abstracts_en_visible") == 0
        and rights.get("translations_cn_visible") == 0
        and [a.get("doi") for a in articles] == DOIS
        and all(
            a.get("sequence") == i
            and a.get("article_type") == "research-article"
            and a.get("public_rights", {}).get("status") == "withheld"
            and a.get("translation", {}).get("status") == "rights_withheld"
            and not any(a.get(k) for k in ("title_cn", "abstract_en", "abstract_cn"))
            for i, a in enumerate(articles, 1)
        )
    )


def repair(data_root: Path) -> list[str]:
    parent = data_root / "public/api/v1/journals/ijio/issues"
    paths = [parent / "current.json", parent / f"{ISSUE}.json"]
    if not all(p.is_file() for p in paths):
        raise ValueError("both canonical IJIO seed paths are required")
    payloads = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    current, immutable = payloads
    if current != immutable:
        raise ValueError("current and immutable IJIO seed snapshots diverged")
    if not eligible(current):
        raise ValueError("IJIO seeded source/order/public rights no longer match approved evidence")
    if current.get("publication_date") == OFFICIAL_PERIOD:
        return []
    if current.get("publication_date") != OLD_PERIOD:
        raise ValueError("unexpected IJIO date; no unbounded correction")
    updated = copy.deepcopy(current)
    updated["publication_date"] = OFFICIAL_PERIOD
    if not eligible(updated):
        raise ValueError("corrected issue lost source/rights identity")
    output = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    for p in paths:
        p.write_text(output, encoding="utf-8")
    return [str(p.relative_to(data_root)) for p in paths]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    changed = repair(args.data_root)
    print(json.dumps({
        "journal": "IJIO", "issue": ISSUE,
        "official_period": OFFICIAL_PERIOD,
        "changed_paths": changed,
        "enabled_changed": False,
        "rights_grants_changed": False,
    }))


if __name__ == "__main__":
    main()
