from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import html
import json
import os
from pathlib import Path
import re
import smtplib
import sys
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.china_relevance import classify_china_relevance


DEFAULT_PUBLIC_ROOT = ROOT / "public" / "api" / "v1" / "journals"
DEFAULT_STATE = ROOT / "data" / "monitoring" / "email-notifications.json"
DEFAULT_OUTCOME = ROOT / "output" / "email-notification-result.json"
SITE_ROOT = "https://academic-door.github.io/journals"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default or {})
    return payload if isinstance(payload, dict) else dict(default or {})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def issue_fingerprint(issue: dict[str, Any]) -> str:
    article_ids = sorted(
        str(article.get("paper_id") or article.get("doi") or article.get("source_url"))
        for article in issue.get("articles", [])
        if article.get("paper_id") or article.get("doi") or article.get("source_url")
    )
    material = "|".join([str(issue.get("issue_id", "")), *article_ids])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def china_related_count(issue: dict[str, Any]) -> int:
    count = 0
    for article in issue.get("articles", []):
        relevance = article.get("china_relevance")
        if not isinstance(relevance, dict):
            relevance = classify_china_relevance(article)
        if relevance.get("status") == "yes":
            count += 1
    return count


def _journal_collections() -> dict[str, list[str]]:
    config = yaml.safe_load(
        (ROOT / "config" / "journals.yml").read_text(encoding="utf-8")
    )
    return {
        str(item.get("id", "")).lower(): list(item.get("collections", []))
        for item in config.get("journals", {}).values()
        if item.get("id")
    }


def _issue_link(journal_id: str, collections: dict[str, list[str]]) -> str:
    section = "" if "top5" in collections.get(journal_id, []) else "fields/"
    return f"{SITE_ROOT}/{section}?journal={journal_id}"


def issue_snapshot(
    issue: dict[str, Any],
    collections: dict[str, list[str]],
) -> dict[str, Any]:
    journal_id = str(issue.get("journal_id", "")).lower()
    article_count = int(
        issue.get("research_article_count") or len(issue.get("articles", []))
    )
    return {
        "journal_id": journal_id,
        "journal_name": str(issue.get("journal_name", journal_id.upper())),
        "issue_id": str(issue.get("issue_id", "")),
        "issue_label": str(
            issue.get("issue_label")
            or f"Vol. {issue.get('volume', '')} · No. {issue.get('issue', '')}"
        ),
        "publication_date": str(issue.get("publication_date", "")),
        "article_count": article_count,
        "china_related_count": china_related_count(issue),
        "fingerprint": issue_fingerprint(issue),
        "directory_url": _issue_link(journal_id, collections),
        "composer_url": f"{SITE_ROOT}/composer/?journal={journal_id}",
    }


def load_current_issues(public_root: Path) -> dict[str, dict[str, Any]]:
    collections = _journal_collections()
    snapshots: dict[str, dict[str, Any]] = {}
    for path in sorted(public_root.glob("*/issues/current.json")):
        issue = read_json(path)
        if (
            issue.get("status") != "ready"
            or not issue.get("issue_id")
            or not issue.get("articles")
        ):
            continue
        snapshot = issue_snapshot(issue, collections)
        snapshots[snapshot["journal_id"]] = snapshot
    return snapshots


@dataclass(frozen=True)
class SMTPSettings:
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str
    recipients: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> SMTPSettings | None:
        host = os.environ.get("SMTP_HOST", "").strip()
        security = (os.environ.get("SMTP_SECURITY") or "starttls").strip().lower()
        username = os.environ.get("SMTP_USERNAME", "").strip()
        password = os.environ.get("SMTP_PASSWORD", "")
        sender = os.environ.get("SMTP_FROM", "").strip() or username
        recipient_text = os.environ.get("NOTIFICATION_EMAIL_TO", "")
        recipients = tuple(
            value.strip()
            for value in re.split(r"[,;]", recipient_text)
            if value.strip()
        )
        if not host or not sender or not recipients:
            return None
        if bool(username) != bool(password):
            return None
        if security not in {"starttls", "ssl"}:
            return None
        default_port = 465 if security == "ssl" else 587
        try:
            port = int(os.environ.get("SMTP_PORT", "") or default_port)
        except ValueError:
            return None
        return cls(
            host=host,
            port=port,
            security=security,
            username=username,
            password=password,
            sender=sender,
            recipients=recipients,
        )


def build_message(events: list[dict[str, Any]], settings: SMTPSettings) -> EmailMessage:
    count = len(events)
    subject = (
        f"[Academic Door] {events[0]['journal_name']} 新卷期已就绪"
        if count == 1
        else f"[Academic Door] {count} 本期刊新卷期已就绪"
    )
    plain_lines = [
        "Academic Door 已完成期刊数据采集、双语整理、质量检查和 Composer 同步。",
        "",
    ]
    html_items: list[str] = []
    for event in events:
        event_label = "本期补录" if event.get("event_type") == "supplement" else "新卷期"
        period = f" · {event['publication_date']}" if event["publication_date"] else ""
        summary = (
            f"{event['issue_label']}{period} · {event['article_count']} 篇"
            f" · 中国相关 {event['china_related_count']} 篇"
        )
        plain_lines.extend(
            [
                f"{event['journal_name']}（{event_label}）",
                summary,
                f"查看目录：{event['directory_url']}",
                f"打开编辑器：{event['composer_url']}",
                "",
            ]
        )
        html_items.append(
            "<li style=\"margin:0 0 18px;\">"
            f"<strong>{html.escape(event['journal_name'])}</strong>"
            f"（{event_label}）<br>"
            f"{html.escape(summary)}<br>"
            f"<a href=\"{html.escape(event['directory_url'])}\">查看目录</a>"
            "　"
            f"<a href=\"{html.escape(event['composer_url'])}\">打开微信公众号编辑器</a>"
            "</li>"
        )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message.set_content("\n".join(plain_lines))
    message.add_alternative(
        "<p>Academic Door 已完成期刊数据采集、双语整理、质量检查和 "
        "Composer 同步。</p><ul>"
        + "".join(html_items)
        + "</ul>",
        subtype="html",
    )
    return message


def send_message(message: EmailMessage, settings: SMTPSettings) -> None:
    smtp_class = smtplib.SMTP_SSL if settings.security == "ssl" else smtplib.SMTP
    with smtp_class(settings.host, settings.port, timeout=30) as client:
        if settings.security == "starttls":
            client.starttls()
        if settings.username:
            client.login(settings.username, settings.password)
        client.send_message(message)


def synchronize(
    *,
    public_root: Path,
    state_path: Path,
    composer_status: str,
    settings: SMTPSettings | None,
    transport: Callable[[EmailMessage, SMTPSettings], None] = send_message,
) -> dict[str, Any]:
    state = read_json(
        state_path,
        {
            "schema_version": "1.0",
            "updated_at": "",
            "known": {},
            "pending": {},
            "sent": {},
        },
    )
    known = state.setdefault("known", {})
    pending = state.setdefault("pending", {})
    sent = state.setdefault("sent", {})
    snapshots = load_current_issues(public_root)
    first_run = not known and not pending and not sent

    for journal_id, snapshot in snapshots.items():
        previous = known.get(journal_id)
        if first_run:
            known[journal_id] = snapshot
            continue
        if previous and previous.get("fingerprint") == snapshot["fingerprint"]:
            continue
        event = dict(snapshot)
        event["event_type"] = (
            "supplement"
            if previous and previous.get("issue_id") == snapshot["issue_id"]
            else "new_issue"
        )
        event["detected_at"] = now_iso()
        event["composer_ready"] = composer_status == "success"
        pending[journal_id] = event
        known[journal_id] = snapshot

    if composer_status == "success":
        for event in pending.values():
            event["composer_ready"] = True

    ready = [
        event
        for event in pending.values()
        if event.get("composer_ready")
        and sent.get(event["journal_id"], {}).get("fingerprint")
        != event.get("fingerprint")
    ]
    ready.sort(key=lambda item: (item.get("publication_date", ""), item["journal_id"]))
    outcome: dict[str, Any] = {
        "status": "idle",
        "queued": len(pending),
        "sent": 0,
        "journals": [],
    }

    if first_run:
        outcome["status"] = "seeded"
    elif ready and settings is None:
        outcome["status"] = "unconfigured"
    elif ready:
        try:
            transport(build_message(ready, settings), settings)
        except Exception as error:  # SMTP libraries expose many provider errors.
            outcome["status"] = "failure"
            outcome["error_type"] = type(error).__name__
        else:
            sent_at = now_iso()
            for event in ready:
                sent[event["journal_id"]] = {
                    "issue_id": event["issue_id"],
                    "fingerprint": event["fingerprint"],
                    "sent_at": sent_at,
                }
                pending.pop(event["journal_id"], None)
            outcome.update(
                {
                    "status": "sent",
                    "queued": len(pending),
                    "sent": len(ready),
                    "journals": [event["journal_id"] for event in ready],
                }
            )
    elif pending:
        outcome["status"] = "waiting_for_composer"

    state["updated_at"] = now_iso()
    write_json(state_path, state)
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--outcome", type=Path, default=DEFAULT_OUTCOME)
    parser.add_argument(
        "--composer-status",
        choices=["success", "failure", "skipped"],
        default="skipped",
    )
    args = parser.parse_args()
    outcome = synchronize(
        public_root=args.public_root,
        state_path=args.state,
        composer_status=args.composer_status,
        settings=SMTPSettings.from_environment(),
    )
    write_json(args.outcome, outcome)
    print(
        json.dumps(
            {
                "status": outcome["status"],
                "queued": outcome["queued"],
                "sent": outcome["sent"],
                "journals": outcome["journals"],
            },
            ensure_ascii=False,
        )
    )
    return 1 if outcome["status"] == "failure" else 0


if __name__ == "__main__":
    raise SystemExit(main())
