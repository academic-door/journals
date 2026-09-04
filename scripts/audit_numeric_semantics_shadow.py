from __future__ import annotations

"""Shadow-only semantic numeric audit (non-gating).

This tool applies the semantic numeric canonicalizer to the same translated
article corpus that the production release audit uses, and reports every
semantic quantity mismatch WITHOUT changing the production release gate.

The semantic capability is explicitly a shadow: it never approves or blocks a
publication.  Findings are always reported and the process still exits 0.  A
non-zero exit indicates the shadow tool itself is broken (config/import/logic
failure), not that the data is bad.

Output: prints a summary to stdout and writes SHADOW_NUMERIC_BASELINE_V1.json
(unavoidably regenerated per run; not part of the release gate).
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.article_types import translation_is_complete
from scripts.translate_issue import _source_hash, resolve_semantic_quantities


def audit() -> int:
    config = yaml.safe_load(
        (ROOT / "config" / "journals.yml").read_text(encoding="utf-8")
    )
    enabled = [
        journal
        for journal in config["journals"].values()
        if journal.get("enabled")
    ]

    findings: list[dict[str, Any]] = []
    scanned = 0
    for journal in enabled:
        current = (
            ROOT
            / "public"
            / "api"
            / "v1"
            / "journals"
            / journal["id"]
            / "issues"
            / "current.json"
        )
        if not current.exists():
            continue
        issue = json.loads(current.read_text(encoding="utf-8"))
        for article in issue.get("articles", []):
            # Mirrors audit_public_data the same relevant corpus: a translated
            # article with an English abstract to compare against.
            if not article.get("abstract_en"):
                continue
            if not translation_is_complete(article):
                continue
            scanned += 1
            source_text = (
                f"{article.get('title_en', '')}\n{article.get('abstract_en', '')}"
            )
            translated_text = (
                f"{article.get('title_cn', '')}\n{article.get('abstract_cn', '')}"
            )
            source_q, translated_q = resolve_semantic_quantities(
                source_text, translated_text
            )
            if source_q != translated_q:
                findings.append(
                    {
                        "journal": journal["id"],
                        "doi": article.get("doi") or article.get("paper_id"),
                        "source_hash": _source_hash(article),
                        "source_quantities": dict(sorted(source_q.items())),
                        "translation_quantities": dict(
                            sorted(translated_q.items())
                        ),
                        "missing": list((source_q - translated_q).elements()),
                        "added": list((translated_q - source_q).elements()),
                    }
                )

    report = {
        "schema_version": "1.0",
        "scope": "shadow-semantic-numeric",
        "total_articles_scanned": scanned,
        "semantic_mismatch_count": len(findings),
        "findings": findings,
    }
    (ROOT / "SHADOW_NUMERIC_BASELINE_V1.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "shadow numeric audit: "
        f"{scanned} translated articles scanned, "
        f"{len(findings)} semantic mismatches (shadow only, non-gating)"
    )
    # Findings are non-blocking; only a real failure raises earlier.
    return 0


if __name__ == "__main__":
    raise SystemExit(audit())
