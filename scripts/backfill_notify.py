"""Send an email summary after each field-history backfill batch.

The backfill workflow writes a batch report JSON plus the resumable state file;
this script turns them into a short email so operators are notified without
polling GitHub or waiting for a chat turn.
"""

from __future__ import annotations

import argparse
import json
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from scripts.email_notifications import SMTPSettings, send_message


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"issues": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"issues": {}}


def load_report(path: str) -> dict[str, Any]:
    if not path:
        return {}
    report_path = Path(path)
    if not report_path.exists():
        return {}
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def status_counts(state: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {
        "complete": 0,
        "translation_partial": 0,
        "blocked": 0,
        "pending": 0,
    }
    for entry in state.get("issues", {}).values():
        status = str(entry.get("status", "") or "pending")
        counts[status if status in counts else "pending"] += 1
    return counts


def build_message(
    counts: dict[str, int],
    report: dict[str, Any],
    settings: SMTPSettings,
) -> EmailMessage:
    total = sum(counts.values())
    subject = (
        "[Academic Door] 历史回填批次完成："
        f"完成 {counts['complete']} · 部分 {counts['translation_partial']} · "
        f"被拦 {counts['blocked']}"
    )
    lines = [
        "Academic Door 领域刊 2025-2026 历史回填批次已结束。",
        "",
        f"进度：共 {total} 卷 — 完成 {counts['complete']}，"
        f"部分翻译 {counts['translation_partial']}，"
        f"被守卫拦下（需浏览器核验）{counts['blocked']}，"
        f"未开始 {counts['pending']}。",
        "",
    ]
    results = report.get("results", [])
    if results:
        lines.append("本批结果：")
        for item in results:
            issue_id = item.get("issue_id", "")
            result = item.get("result", "")
            detail = item.get("error", "")
            lines.append(f"- {issue_id}: {result}" + (f" ({detail})" if detail else ""))
        lines.append("")
    lines.extend(
        [
            "状态看板：https://academic-door.github.io/journals/api/v1/backfill-status.md",
            "状态 JSON：https://academic-door.github.io/journals/api/v1/backfill-status.json",
            "Actions：https://github.com/academic-door/journals/actions/workflows/backfill-field-history.yml",
        ]
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message.set_content("\n".join(lines))
    return message


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    counts = status_counts(load_state(Path(args.state)))
    report = load_report(args.report)
    settings = SMTPSettings.from_environment()
    if settings is None:
        print("SMTP not configured; skip email")
        return 0
    message = build_message(counts, report, settings)
    send_message(message, settings)
    print("email sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
