from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests


API = "https://api.github.com"
TITLE_PREFIX = "[journal-monitor]"


def _request(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    response = session.request(method, url, timeout=30, **kwargs)
    response.raise_for_status()
    return response


def sync_alerts(
    result: dict[str, Any],
    *,
    repository: str,
    token: str,
    session: requests.Session | None = None,
) -> dict[str, list[str]]:
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
    open_issues = {
        str(issue.get("title", "")): issue
        for issue in response.json()
        if "pull_request" not in issue
    }
    created: list[str] = []
    closed: list[str] = []
    for journal in result.get("alerts", {}).get("newly_alerting", []):
        title = f"{TITLE_PREFIX} {journal} 连续更新失败"
        if title in open_issues:
            continue
        _request(
            client,
            "POST",
            f"{API}/repos/{repository}/issues",
            json={
                "title": title,
                "body": (
                    f"{journal} 已连续三次无法完成轻量探测或深度更新。"
                    "线上继续保留上一份已验证数据；请查看最近的 "
                    "`Monitor journal updates` Actions 日志。"
                ),
            },
        )
        created.append(journal)
    for journal in result.get("alerts", {}).get("recovered", []):
        title = f"{TITLE_PREFIX} {journal} 连续更新失败"
        issue = open_issues.get(title)
        if not issue:
            continue
        _request(
            client,
            "PATCH",
            f"{API}/repos/{repository}/issues/{issue['number']}",
            json={
                "state": "closed",
                "state_reason": "completed",
                "body": issue.get("body", ""),
            },
        )
        closed.append(journal)
    return {"created": created, "closed": closed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY", "")
    )
    args = parser.parse_args()
    if not args.repository:
        raise SystemExit("GITHUB_REPOSITORY is required")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    synced = sync_alerts(
        result,
        repository=args.repository,
        token=os.environ.get("GITHUB_TOKEN", ""),
    )
    print(json.dumps(synced, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
