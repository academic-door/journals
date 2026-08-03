"""Auditable ledger for human/browser-confirmed official issue orders.

A collector that reaches the official issue page proves itself every run: if the
publisher blocks it tomorrow, the flag drops automatically. A human confirming
the same page through a logged-in browser does not have that property — the
claim is persisted by the provenance guard and stays true until the article set
changes, which for a numbered issue may be never.

This ledger is what keeps that second kind of claim honest. It stores a compact,
publishable record per verification — no page HTML, no credentials, no full
text — so that anyone can see when a journal was confirmed, against which URL,
and whether that confirmation is still within its validity window.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "data" / "provenance" / "order-verification.json"
# A one-off human confirmation should not outlive the issue it looked at.
DEFAULT_VALIDITY_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def sequence_digest(identifiers: Iterable[str]) -> str:
    """Stable digest of the confirmed article order.

    Storing the digest instead of the identifier list keeps the ledger small and
    still lets a later run prove it is looking at the same sequence.
    """

    joined = "\n".join(str(value).strip().lower() for value in identifiers)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def load_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1.0", "entries": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        raise ValueError(f"{path}: ledger must be an object with an entries map")
    return payload


def save_ledger(ledger: dict[str, Any], path: Path = LEDGER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def record_verification(
    ledger: dict[str, Any],
    *,
    journal_id: str,
    issue_id: str,
    official_url: str,
    identifiers: Iterable[str],
    captured_at: str | None = None,
    method: str = "browser-authorized",
    validity_days: int = DEFAULT_VALIDITY_DAYS,
) -> dict[str, Any]:
    captured = captured_at or _now().isoformat()
    ordered = list(identifiers)
    entry = {
        "journal_id": journal_id,
        "issue_id": issue_id,
        "official_url": official_url,
        "captured_at": captured,
        "method": method,
        "item_count": len(ordered),
        "sequence_sha256": sequence_digest(ordered),
        "expires_at": (
            datetime.fromisoformat(captured.replace("Z", "+00:00"))
            + timedelta(days=validity_days)
        )
        .replace(microsecond=0)
        .isoformat(),
    }
    ledger.setdefault("entries", {})[journal_id] = entry
    return entry


def is_current(entry: dict[str, Any], *, now: datetime | None = None) -> bool:
    expires = str(entry.get("expires_at", ""))
    if not expires:
        return False
    try:
        deadline = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return (now or _now()) <= deadline


def audit_claims(
    claims: dict[str, dict[str, Any]],
    ledger: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[str]:
    """Return one finding per journal whose verified claim lacks live evidence.

    ``claims`` maps journal_id to the published ``quality`` object.
    """

    findings: list[str] = []
    entries = ledger.get("entries", {})
    for journal_id, quality in sorted(claims.items()):
        marker = quality.get("browser_order_verification")
        if not isinstance(marker, dict) or not marker:
            continue
        entry = entries.get(journal_id)
        if not entry:
            findings.append(
                f"{journal_id}: claims browser-confirmed order but the "
                "provenance ledger has no record of it"
            )
            continue
        if entry.get("issue_id") and quality.get("issue_id"):
            if entry["issue_id"] != quality["issue_id"]:
                findings.append(
                    f"{journal_id}: ledger records {entry['issue_id']} but the "
                    f"published issue is {quality['issue_id']}"
                )
                continue
        if not is_current(entry, now=now):
            findings.append(
                f"{journal_id}: browser confirmation expired on "
                f"{entry.get('expires_at', 'unknown')}; re-confirm or let the "
                "order fall back to pending_official"
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="Record one confirmed issue order.")
    record.add_argument("--journal-id", required=True)
    record.add_argument("--issue-id", required=True)
    record.add_argument("--official-url", required=True)
    record.add_argument(
        "--identifiers",
        required=True,
        help="Comma or newline separated PII/DOI list in official order.",
    )
    record.add_argument("--captured-at", default=None)
    record.add_argument("--validity-days", type=int, default=DEFAULT_VALIDITY_DAYS)

    sub.add_parser("list", help="Print the ledger with freshness.")

    args = parser.parse_args()
    ledger = load_ledger()
    if args.command == "record":
        identifiers = [
            value.strip()
            for value in args.identifiers.replace("\n", ",").split(",")
            if value.strip()
        ]
        entry = record_verification(
            ledger,
            journal_id=args.journal_id,
            issue_id=args.issue_id,
            official_url=args.official_url,
            identifiers=identifiers,
            captured_at=args.captured_at,
            validity_days=args.validity_days,
        )
        save_ledger(ledger)
        print(json.dumps(entry, ensure_ascii=False))
        return 0

    for journal_id, entry in sorted(ledger.get("entries", {}).items()):
        state = "current" if is_current(entry) else "expired"
        print(
            f"{journal_id}\t{entry.get('issue_id', '-')}\t{state}\t"
            f"{entry.get('expires_at', '-')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
