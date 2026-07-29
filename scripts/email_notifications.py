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
STATE_SCHEMA_VERSION = "1.2"


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
    quality = issue.get("quality") if isinstance(issue.get("quality"), dict) else {}
    abstract_count = int(quality.get("abstract_en_complete") or 0)
    translation_count = int(quality.get("translation_complete") or 0)
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
        "abstract_count": abstract_count,
        "translation_count": translation_count,
        "china_related_count": china_related_count(issue),
        "fingerprint": issue_fingerprint(issue),
        "directory_url": _issue_link(journal_id, collections),
        "composer_url": (
            f"{SITE_ROOT}/composer/?journal={journal_id}"
            f"&issue={issue.get('issue_id', '')}"
        ),
    }


def load_issue_snapshots(
    public_root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    collections = _journal_collections()
    detected: dict[str, dict[str, Any]] = {}
    ready: dict[str, dict[str, Any]] = {}
    for path in sorted(public_root.glob("*/issues/current.json")):
        issue = read_json(path)
        if not issue.get("issue_id") or not issue.get("articles"):
            continue
        snapshot = issue_snapshot(issue, collections)
        ready[snapshot["journal_id"]] = snapshot
    for path in sorted(public_root.glob("*/issues/detected.json")):
        issue = read_json(path)
        if not issue.get("issue_id") or not issue.get("articles"):
            continue
        snapshot = issue_snapshot(issue, collections)
        detected[snapshot["journal_id"]] = snapshot
    for journal_id, snapshot in ready.items():
        detected.setdefault(journal_id, snapshot)
    return detected, ready


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
    stage = str(events[0].get("notification_stage") or "ready")
    ready_stage = stage == "ready"
    stage_label = "新卷期已就绪" if ready_stage else "发现新卷期"
    subject = (
        f"[Academic Door] {events[0]['journal_name']} {stage_label}"
        if count == 1
        else f"[Academic Door] {count} 本期刊{stage_label}"
    )
    intro = (
        "Academic Door 已完成期刊数据采集、双语整理、质量检查和 Composer 同步。"
        if ready_stage
        else "Academic Door 已发现新的官方卷期，正在继续补齐英文摘要和中文内容。"
    )
    plain_lines = [intro, ""]
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
                *(
                    []
                    if ready_stage
                    else [
                        (
                            f"整理进度：英文摘要 {event.get('abstract_count', 0)}/"
                            f"{event['article_count']} · 中文内容 "
                            f"{event.get('translation_count', 0)}/{event['article_count']}"
                        )
                    ]
                ),
                f"查看目录：{event['directory_url']}",
                *(
                    [f"打开编辑器：{event['composer_url']}"]
                    if ready_stage
                    else []
                ),
                "",
            ]
        )
        progress = (
            ""
            if ready_stage
            else (
                f"<br>整理进度：英文摘要 {event.get('abstract_count', 0)}/"
                f"{event['article_count']} · 中文内容 "
                f"{event.get('translation_count', 0)}/{event['article_count']}"
            )
        )
        composer_link = (
            "　"
            f"<a href=\"{html.escape(event['composer_url'])}\">打开微信公众号编辑器</a>"
            if ready_stage
            else ""
        )
        html_items.append(
            "<li style=\"margin:0 0 18px;\">"
            f"<strong>{html.escape(event['journal_name'])}</strong>"
            f"（{event_label}）<br>"
            f"{html.escape(summary)}{progress}<br>"
            f"<a href=\"{html.escape(event['directory_url'])}\">查看目录</a>"
            f"{composer_link}"
            "</li>"
        )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message.set_content("\n".join(plain_lines))
    message.add_alternative(
        f"<p>{html.escape(intro)}</p><ul>"
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


def send_test_notification(
    settings: SMTPSettings,
    issue: dict[str, Any] | None = None,
    transport: Callable[[EmailMessage, SMTPSettings], None] = send_message,
) -> None:
    if issue is None:
        message = EmailMessage()
        message["Subject"] = "[Academic Door] 期刊更新邮件通知测试成功"
        message["From"] = settings.sender
        message["To"] = ", ".join(settings.recipients)
        message.set_content(
            "Academic Door 已成功连接发件邮箱。\n\n"
            "以后新卷期通过数据质量检查并同步到 Composer 后，"
            "你会在这里收到期刊、卷期、文章数、中国相关篇数及直达链接。"
        )
    else:
        collections = _journal_collections()
        event = issue_snapshot(issue, collections)
        event["event_type"] = "new_issue"
        message = build_message([event], settings)
        message.replace_header(
            "Subject",
            f"[测试预览] {message['Subject']}",
        )
    transport(message, settings)


def synchronize(
    *,
    public_root: Path,
    state_path: Path,
    composer_status: str,
    settings: SMTPSettings | None,
    transport: Callable[[EmailMessage, SMTPSettings], None] = send_message,
) -> dict[str, Any]:
    empty_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": "",
        "known_detected": {},
        "known_ready": {},
        "pending_detected": {},
        "pending_ready": {},
        "sent_detected": {},
        "sent_ready": {},
    }
    state = read_json(
        state_path,
        empty_state,
    )
    if state.get("schema_version") == "1.1":
        legacy_known = dict(state.get("known") or {})
        legacy_pending = dict(state.get("pending") or {})
        legacy_sent = dict(state.get("sent") or {})
        for event in legacy_pending.values():
            event["notification_stage"] = "ready"
        state = {
            **empty_state,
            "updated_at": state.get("updated_at", ""),
            "known_detected": dict(legacy_known),
            "known_ready": dict(legacy_known),
            "pending_ready": legacy_pending,
            "sent_detected": dict(legacy_sent),
            "sent_ready": dict(legacy_sent),
        }
    elif state.get("schema_version") != STATE_SCHEMA_VERSION:
        state = dict(empty_state)
    known_detected = state.setdefault("known_detected", {})
    known_ready = state.setdefault("known_ready", {})
    pending_detected = state.setdefault("pending_detected", {})
    pending_ready = state.setdefault("pending_ready", {})
    sent_detected = state.setdefault("sent_detected", {})
    sent_ready = state.setdefault("sent_ready", {})
    detected_snapshots, ready_snapshots = load_issue_snapshots(public_root)
    first_run = not any(
        (
            known_detected,
            known_ready,
            pending_detected,
            pending_ready,
            sent_detected,
            sent_ready,
        )
    )

    if first_run:
        known_detected.update(detected_snapshots)
        known_ready.update(ready_snapshots)
    else:
        for journal_id, snapshot in detected_snapshots.items():
            previous = known_detected.get(journal_id)
            if previous and previous.get("fingerprint") == snapshot["fingerprint"]:
                continue
            event = dict(snapshot)
            event["event_type"] = (
                "supplement"
                if previous and previous.get("issue_id") == snapshot["issue_id"]
                else "new_issue"
            )
            event["notification_stage"] = "detected"
            event["detected_at"] = now_iso()
            pending_detected[journal_id] = event
            known_detected[journal_id] = snapshot

        for journal_id, snapshot in ready_snapshots.items():
            previous = known_ready.get(journal_id)
            if previous and previous.get("fingerprint") == snapshot["fingerprint"]:
                continue
            event = dict(snapshot)
            event["event_type"] = (
                "supplement"
                if previous and previous.get("issue_id") == snapshot["issue_id"]
                else "new_issue"
            )
            event["notification_stage"] = "ready"
            event["detected_at"] = now_iso()
            event["composer_ready"] = composer_status == "success"
            pending_ready[journal_id] = event
            known_ready[journal_id] = snapshot
            pending_detection = pending_detected.get(journal_id)
            if (
                pending_detection
                and pending_detection.get("fingerprint") == snapshot["fingerprint"]
            ):
                pending_detected.pop(journal_id, None)

    if composer_status == "success":
        for event in pending_ready.values():
            event["composer_ready"] = True

    detection_events = [
        event
        for event in pending_detected.values()
        if sent_detected.get(event["journal_id"], {}).get("fingerprint")
        != event.get("fingerprint")
    ]
    ready_events = [
        event
        for event in pending_ready.values()
        if event.get("composer_ready")
        and sent_ready.get(event["journal_id"], {}).get("fingerprint")
        != event.get("fingerprint")
    ]
    detection_events.sort(
        key=lambda item: (item.get("publication_date", ""), item["journal_id"])
    )
    ready_events.sort(
        key=lambda item: (item.get("publication_date", ""), item["journal_id"])
    )
    deliverable = detection_events + ready_events
    outcome: dict[str, Any] = {
        "status": "idle",
        "queued": len(pending_detected) + len(pending_ready),
        "sent": 0,
        "journals": [],
        "messages": 0,
    }

    if first_run:
        outcome["status"] = "seeded"
    elif deliverable and settings is None:
        outcome["status"] = "unconfigured"
    elif deliverable:
        try:
            if detection_events:
                transport(build_message(detection_events, settings), settings)
            if ready_events:
                transport(build_message(ready_events, settings), settings)
        except Exception as error:  # SMTP libraries expose many provider errors.
            outcome["status"] = "failure"
            outcome["error_type"] = type(error).__name__
        else:
            sent_at = now_iso()
            for event in detection_events:
                sent_detected[event["journal_id"]] = {
                    "issue_id": event["issue_id"],
                    "fingerprint": event["fingerprint"],
                    "sent_at": sent_at,
                }
                pending_detected.pop(event["journal_id"], None)
            for event in ready_events:
                sent_ready[event["journal_id"]] = {
                    "issue_id": event["issue_id"],
                    "fingerprint": event["fingerprint"],
                    "sent_at": sent_at,
                }
                pending_ready.pop(event["journal_id"], None)
            outcome.update(
                {
                    "status": "sent",
                    "queued": len(pending_detected) + len(pending_ready),
                    "sent": len(deliverable),
                    "journals": sorted(
                        {event["journal_id"] for event in deliverable}
                    ),
                    "messages": int(bool(detection_events))
                    + int(bool(ready_events)),
                }
            )
    elif pending_detected or pending_ready:
        outcome["status"] = "waiting_for_composer"

    state["updated_at"] = now_iso()
    state["schema_version"] = STATE_SCHEMA_VERSION
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
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send one private configuration test without changing notification state.",
    )
    parser.add_argument(
        "--test-journal",
        default="",
        help="With --test-email, render this journal's current issue as a sample.",
    )
    args = parser.parse_args()
    settings = SMTPSettings.from_environment()
    if args.test_email:
        if settings is None:
            print(json.dumps({"status": "unconfigured"}))
            return 2
        try:
            issue = None
            if args.test_journal:
                journal_id = re.sub(r"[^a-z0-9_-]", "", args.test_journal.lower())
                issue_path = (
                    args.public_root / journal_id / "issues" / "current.json"
                )
                issue = read_json(issue_path)
                if not issue.get("issue_id") or not issue.get("articles"):
                    print(
                        json.dumps(
                            {
                                "status": "missing_issue",
                                "journal": journal_id,
                            }
                        )
                    )
                    return 3
            send_test_notification(settings, issue=issue)
        except Exception as error:
            print(
                json.dumps(
                    {"status": "failure", "error_type": type(error).__name__}
                )
            )
            return 1
        print(json.dumps({"status": "sent"}))
        return 0
    outcome = synchronize(
        public_root=args.public_root,
        state_path=args.state,
        composer_status=args.composer_status,
        settings=settings,
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
