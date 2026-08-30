"""Turn a Wiley citation export into a conservative official roster evidence file.

Citation exports are roster evidence only: they never invent abstracts or authors.
Existing article content is enriched later by the normal evidence/import gates.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _clean(value: str) -> str:
    return " ".join(value.strip().split())


def parse_ris(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_tag = ""
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        match = re.match(r"^([A-Z0-9]{2})\s+-\s+(.*)$", line)
        if not match:
            if current is not None and last_tag:
                current[last_tag] = _clean(
                    f"{current.get(last_tag, '')} {line}"
                )
            continue
        tag, value = match.groups()
        value = _clean(value)
        if tag == "TY":
            if current is not None:
                records.append(current)
            current = {"TY": value, "AU": []}
        elif current is not None:
            last_tag = tag
            if tag == "AU":
                current.setdefault("AU", []).append(value)
            else:
                current[tag] = value
        if tag == "ER" and current is not None:
            records.append(current)
            current = None
            last_tag = ""
    if current is not None:
        records.append(current)
    result: list[dict[str, Any]] = []
    for record in records:
        doi = _clean(str(record.get("DO", ""))).lower()
        title = _clean(str(record.get("TI", "")))
        if doi and title:
            result.append({"doi": doi, "title_en": title})
    return result


def build_evidence(
    text: str,
    *,
    issue_id: str,
    official_url: str,
    captured_at: str | None = None,
) -> dict[str, Any]:
    items = parse_ris(text)
    if not items:
        raise ValueError("Wiley citation export contains no DOI/title records")
    return {
        "schema_version": "1.0",
        "capture_mode": "official-roster-evidence",
        "method": "browser-authorized",
        "finalized": True,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
        "journal_id": issue_id.split("-", 1)[0],
        "issue_id": issue_id,
        "official_url": official_url,
        "items": [
            {"sequence": index, **item}
            for index, item in enumerate(items, start=1)
        ],
        "excluded_items": [],
        "excluded_item_count": 0,
        "notes": "Wiley citation export is roster-only; no abstract or author was inferred.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--issue-id", required=True)
    parser.add_argument("--official-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = build_evidence(
        args.input.read_text(encoding="utf-8"),
        issue_id=args.issue_id,
        official_url=args.official_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"issue_id": args.issue_id, "item_count": len(evidence["items"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
