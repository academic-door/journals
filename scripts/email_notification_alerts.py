from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests


API = "https://api.github.com"
ALERT_TITLE = "[journal-notify] 私人邮件通知失败"


def _request(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    response = session.request(method, url, timeout=30, **kwargs)
    response.raise_for_status()
    return response


def sync_email_alert(
    outcome: dict[str, Any],
    *,
    repository: str,
    token: str,
    session: requests.Session | None = None,
) -> str:
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    client = session or requests.Session()
    client.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    response = _request(
        client,
        "GET",
        f"{API}/repos/{repository}/issues",
        params={"state": "open", "per_page": 100},
    )
    issue = next(
        (
            item
            for item in response.json()
            if item.get("title") == ALERT_TITLE and "pull_request" not in item
        ),
        None,
    )
    status = outcome.get("status")
    if status == "failure" and issue is None:
        _request(
            client,
            "POST",
            f"{API}/repos/{repository}/issues",
            json={
                "title": ALERT_TITLE,
                "body": (
                    "新卷期已经通过质量检查并同步到 Composer，但私人邮件发送失败。"
                    "待发送事件已保留，后续巡检会继续重试。请检查 SMTP Secrets "
                    "和本轮 GitHub Actions 日志。日志不会输出账号、密码或收件地址。"
                ),
            },
        )
        return "created"
    if status in {"sent", "idle", "seeded"} and issue is not None:
        _request(
            client,
            "PATCH",
            f"{API}/repos/{repository}/issues/{issue['number']}",
            json={"state": "closed", "state_reason": "completed"},
        )
        return "closed"
    return "unchanged"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("outcome", type=Path)
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY", "")
    )
    args = parser.parse_args()
    if not args.repository:
        raise SystemExit("GITHUB_REPOSITORY is required")
    outcome = json.loads(args.outcome.read_text(encoding="utf-8"))
    action = sync_email_alert(
        outcome,
        repository=args.repository,
        token=os.environ.get("GITHUB_TOKEN", ""),
    )
    print(json.dumps({"action": action}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
