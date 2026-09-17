from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
import imaplib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.freshness_announcements import (
    announcement_is_newer,
    normalize_announcement,
)


DEFAULT_CONFIG = ROOT / "config" / "mailbox-announcements.yml"
DEFAULT_STATE = ROOT / "data" / "monitoring" / "state.json"
DEFAULT_PUBLIC_ROOT = ROOT / "public" / "api" / "v1" / "journals"
DEFAULT_RESULT = ROOT / "output" / "mailbox-announcement-result.json"
LOOKBACK_DAYS = 14
MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December"
)
SEASON_PATTERN = r"Spring|Summer|Fall|Autumn|Winter"
IDENTITY_PATTERNS = (
    re.compile(
        r"\b(?:volume|vol\.?)\s*(?P<volume>\d+)\s*"
        r"(?:[,;:/\-–—·]\s*)?"
        r"(?:issue|no\.?|number)\s*(?P<issue>\d+(?:[-–]\d+)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<volume>\d+)\s*\(\s*(?P<issue>\d+)\s*\)",
        re.IGNORECASE,
    ),
)
PERIOD_PATTERNS = (
    re.compile(rf"\b(?P<period>(?:{MONTH_PATTERN})\s+20\d{{2}})\b", re.IGNORECASE),
    re.compile(rf"\b(?P<period>(?:{SEASON_PATTERN})\s+20\d{{2}})\b", re.IGNORECASE),
    re.compile(r"\b(?P<period>20\d{2}-\d{2})\b"),
)
URL_PATTERN = re.compile(r"https://[^\s<>\"')]+", re.IGNORECASE)


class MailboxSettings:
    def __init__(self, *, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "MailboxSettings | None":
        env = os.environ if environment is None else environment
        imap_user = str(env.get("IMAP_USERNAME", "")).strip()
        imap_password = str(env.get("IMAP_PASSWORD", ""))
        if imap_user and imap_password:
            username = imap_user
            password = imap_password
        else:
            username = str(env.get("SMTP_USERNAME", "")).strip()
            password = str(env.get("SMTP_PASSWORD", ""))
        if not username or not password:
            return None
        host = str(env.get("IMAP_HOST", "")).strip().casefold()
        if not host and username.casefold().endswith("@163.com"):
            host = "imap.163.com"
        # Parent #63 authorizes only the dedicated 163 project mailbox.
        if host != "imap.163.com":
            return None
        try:
            port = int(str(env.get("IMAP_PORT", "")).strip() or "993")
        except ValueError:
            return None
        if not 1 <= port <= 65535:
            return None
        return cls(host=host, port=port, username=username, password=password)


def _domain_allowed(domain: str, allowed: list[str]) -> bool:
    normalized = domain.casefold().strip(".")
    return any(
        normalized == item.casefold().strip(".")
        or normalized.endswith("." + item.casefold().strip("."))
        for item in allowed
        if str(item).strip()
    )


def _host_allowed(url: str, allowed: list[str]) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname) and _domain_allowed(
        parsed.hostname or "", allowed
    )


def _message_content(message) -> tuple[str, list[str]]:
    text_parts: list[str] = []
    urls: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if not isinstance(content, str):
            continue
        if content_type == "text/html":
            soup = BeautifulSoup(content, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = str(anchor.get("href", "")).strip()
                if href.startswith("https://"):
                    urls.append(href)
            text_parts.append(soup.get_text(" ", strip=True))
        else:
            text_parts.append(content)
            urls.extend(URL_PATTERN.findall(content))
    return "\n".join(text_parts), list(dict.fromkeys(urls))


def _extract_identity(text: str) -> tuple[str, str] | None:
    identities: set[tuple[str, str]] = set()
    for pattern in IDENTITY_PATTERNS:
        for match in pattern.finditer(text):
            volume = match.group("volume").strip()
            issue = match.group("issue").strip().replace("–", "-")
            identities.add((volume, issue))
    return next(iter(identities)) if len(identities) == 1 else None


def _extract_period(text: str) -> str | None:
    periods: set[str] = set()
    for pattern in PERIOD_PATTERNS:
        for match in pattern.finditer(text):
            raw = " ".join(match.group("period").split())
            periods.add(raw.title() if "-" not in raw else raw)
    return next(iter(periods)) if len(periods) == 1 else None


def _message_observed_at(message) -> str | None:
    raw = str(message.get("Date", "")).strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def extract_signal(raw_message: bytes, rule: dict[str, Any]) -> dict[str, Any] | None:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
    except Exception:
        return None
    addresses = [
        address.strip()
        for _name, address in getaddresses(message.get_all("From", []))
        if address.strip()
    ]
    if len(addresses) != 1 or "@" not in addresses[0]:
        return None
    sender_domain = addresses[0].rsplit("@", 1)[1]
    if not _domain_allowed(sender_domain, list(rule.get("sender_domains", []))):
        return None
    subject = str(message.get("Subject", "")).strip()
    subject_patterns = list(rule.get("subject_patterns", []))
    if not subject_patterns:
        return None
    try:
        if not any(re.search(pattern, subject, re.IGNORECASE) for pattern in subject_patterns):
            return None
    except re.error:
        return None
    body, urls = _message_content(message)
    identity = _extract_identity(subject + "\n" + body)
    period = _extract_period(subject + "\n" + body)
    observed_at = _message_observed_at(message)
    if identity is None or period is None or observed_at is None:
        return None
    official_urls = [
        url.rstrip(".,;)")
        for url in urls
        if _host_allowed(url.rstrip(".,;)"), list(rule.get("official_link_hosts", [])))
    ]
    if not official_urls:
        return None
    preferred_tokens = ("/toc/", "/issue", "/volume/", "/journal/")
    official_urls.sort(
        key=lambda url: (
            not any(token in urlparse(url).path.casefold() for token in preferred_tokens),
            len(url),
        )
    )
    volume, issue = identity
    journal_id = str(rule.get("journal_id", "")).strip().casefold()
    if not journal_id:
        return None
    token = lambda value: re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    volume_token = token(volume)
    issue_token = token(issue)
    if not volume_token or not issue_token:
        return None
    signal = {
        "schema_version": "1.0",
        "journal_id": journal_id,
        "issue_id": f"{journal_id}-{volume_token}-{issue_token}",
        "volume": volume,
        "issue": issue,
        "issue_label": f"Vol. {volume} · No. {issue}",
        "publication_date": period,
        "publication_state": "announced",
        "source_authority": "first_party",
        "source_kind": "official_newsletter",
        "source_url": official_urls[0],
        "observed_at": observed_at,
    }
    return normalize_announcement(journal_id, signal)


def scan_mailbox(
    settings: MailboxSettings,
    rules: list[dict[str, Any]],
    *,
    imap_factory: Callable[[str, int], Any] = imaplib.IMAP4_SSL,
) -> list[dict[str, Any]]:
    client = imap_factory(settings.host, settings.port)
    try:
        client.login(settings.username, settings.password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("mailbox readonly select failed")
        since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        status, payload = client.search(None, "SINCE", since)
        if status != "OK" or not payload:
            raise RuntimeError("mailbox search failed")
        message_ids = payload[0].split()
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for message_id in message_ids:
            status, response = client.fetch(message_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = next(
                (
                    item[1]
                    for item in response
                    if isinstance(item, tuple)
                    and len(item) >= 2
                    and isinstance(item[1], (bytes, bytearray))
                ),
                None,
            )
            if raw is None:
                continue
            matches = [signal for rule in rules if (signal := extract_signal(bytes(raw), rule))]
            journal_ids = {str(signal["journal_id"]) for signal in matches}
            if len(matches) != 1 or len(journal_ids) != 1:
                continue
            signal = matches[0]
            key = (str(signal["journal_id"]), str(signal["issue_id"]))
            previous = found.get(key)
            if previous is None or str(signal["observed_at"]) > str(previous["observed_at"]):
                found[key] = signal
        return sorted(found.values(), key=lambda item: (item["journal_id"], item["issue_id"]))
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _numeric(value: object) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else -1


def _identity_order(item: dict[str, Any] | None) -> tuple[int, int]:
    if not item:
        return (-1, -1)
    return (_numeric(item.get("volume")), _numeric(item.get("issue")))


def apply_signals(
    *,
    state_path: Path,
    public_root: Path,
    signals: list[dict[str, Any] | None],
    rules: list[dict[str, Any]],
) -> list[str]:
    state = _read_json(state_path) or {"schema_version": "1.0", "journals": {}}
    journals = state.setdefault("journals", {})
    rule_by_id = {
        str(rule.get("journal_id", "")).casefold(): rule
        for rule in rules
        if str(rule.get("journal_id", "")).strip()
    }
    changed: list[str] = []
    for raw_signal in signals:
        if not isinstance(raw_signal, dict):
            continue
        journal_id = str(raw_signal.get("journal_id", "")).casefold()
        rule = rule_by_id.get(journal_id)
        if not rule:
            continue
        signal = normalize_announcement(journal_id, raw_signal)
        if not signal or signal.get("source_kind") != "official_newsletter":
            continue
        journal_key = str(rule.get("journal_key", "")).strip() or journal_id.upper()
        current = _read_json(public_root / journal_id / "issues" / "current.json")
        detected = _read_json(public_root / journal_id / "issues" / "detected.json")
        if not announcement_is_newer(signal, current, detected):
            continue
        entry = journals.setdefault(journal_key, {"journal_id": journal_id})
        existing = entry.get("announcement") if isinstance(entry, dict) else None
        if isinstance(existing, dict):
            if str(existing.get("issue_id", "")) == str(signal["issue_id"]):
                continue
            if _identity_order(existing) >= _identity_order(signal):
                continue
        entry["announcement"] = signal
        changed.append(journal_key)
    if changed:
        _write_json(state_path, state)
    return sorted(dict.fromkeys(changed))


def load_rules(path: Path = DEFAULT_CONFIG) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    configured = payload.get("mailbox_announcements", {})
    if not isinstance(configured, dict):
        return []
    rules: list[dict[str, Any]] = []
    for journal_key, raw in configured.items():
        if not isinstance(raw, dict):
            continue
        rule = {**raw, "journal_key": str(journal_key)}
        required_lists = ("sender_domains", "subject_patterns", "official_link_hosts")
        if not str(rule.get("journal_id", "")).strip():
            continue
        if any(not isinstance(rule.get(field), list) or not rule.get(field) for field in required_lists):
            continue
        rules.append(rule)
    return rules


def main() -> int:
    parser = argparse.ArgumentParser(description="Read first-party journal alerts from the dedicated project mailbox.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    args = parser.parse_args()

    rules = load_rules(args.config)
    settings = MailboxSettings.from_environment()
    if settings is None:
        result = {
            "schema_version": "1.0",
            "status": "not_configured",
            "signals": 0,
            "updated_journals": [],
        }
        _write_json(args.result, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    try:
        signals = scan_mailbox(settings, rules)
        changed = apply_signals(
            state_path=args.state,
            public_root=args.public_root,
            signals=signals,
            rules=rules,
        )
    except Exception as error:
        result = {
            "schema_version": "1.0",
            "status": "error",
            "error_type": type(error).__name__,
            "signals": 0,
            "updated_journals": [],
        }
        _write_json(args.result, result)
        print(json.dumps(result, ensure_ascii=False))
        return 1
    result = {
        "schema_version": "1.0",
        "status": "ok",
        "signals": len(signals),
        "updated_journals": changed,
    }
    _write_json(args.result, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
