from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests


API = "https://api.github.com"
TITLE_PREFIX = "[journal-monitor]"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MONITORING = ROOT / "public" / "api" / "v1" / "monitoring.json"


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
    composer_status: str = "skipped",
    reconcile_against: dict[str, Any] | None = None,
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
    # 对账：以 monitoring.json 的当前失败名单为准，关闭已经恢复但计数器没被
    # 清零的陈旧告警。每日全量巡检不写监测状态、也不跑本脚本，若一本刊是被全量
    # 巡检修好的，仅凭 result.alerts.recovered 永远关不掉它的 Issue。
    if reconcile_against is not None:
        still_failing = {
            str(name) for name in reconcile_against.get("failed_journals", [])
        }
        for title, issue in open_issues.items():
            if not title.startswith(TITLE_PREFIX) or "连续更新失败" not in title:
                continue
            journal = title[len(TITLE_PREFIX):].replace("连续更新失败", "").strip()
            if not journal or journal == "Composer" or journal in still_failing:
                continue
            if journal in closed:
                continue
            _request(
                client,
                "PATCH",
                f"{API}/repos/{repository}/issues/{issue['number']}",
                json={"state": "closed", "state_reason": "completed"},
            )
            closed.append(journal)

    composer_title = f"{TITLE_PREFIX} Composer 同步失败"
    composer_issue = open_issues.get(composer_title)
    if composer_status == "failure" and composer_issue is None:
        _request(
            client,
            "POST",
            f"{API}/repos/{repository}/issues",
            json={
                "title": composer_title,
                "body": (
                    "期刊数据已通过审计并写入 data 分支，但同步到私有 Composer "
                    "仓库失败。公开网站继续部署；请检查本轮 Actions 日志以及 "
                    "COMPOSER_DEPLOY_KEY 是否仍为该单一仓库的最小权限写入密钥。"
                ),
            },
        )
        created.append("COMPOSER")
    elif composer_status == "success" and composer_issue is not None:
        _request(
            client,
            "PATCH",
            f"{API}/repos/{repository}/issues/{composer_issue['number']}",
            json={"state": "closed", "state_reason": "completed"},
        )
        closed.append("COMPOSER")
    return {"created": created, "closed": closed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument(
        "--repository", default=os.environ.get("GITHUB_REPOSITORY", "")
    )
    parser.add_argument(
        "--reconcile",
        type=Path,
        default=DEFAULT_MONITORING,
        help=(
            "Path to monitoring.json. Any open journal alert whose journal is "
            "no longer listed as failing there is closed, so the issue tracker "
            "cannot drift away from the data. Defaults to the published "
            "monitoring API, so reconciliation needs no workflow change."
        ),
    )
    parser.add_argument(
        "--no-reconcile",
        action="store_true",
        help="Disable reconciliation and only act on this run's alert events.",
    )
    parser.add_argument(
        "--composer-status",
        choices=["success", "failure", "skipped"],
        default="skipped",
    )
    args = parser.parse_args()
    if not args.repository:
        raise SystemExit("GITHUB_REPOSITORY is required")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    reconcile_against = None
    if not args.no_reconcile and args.reconcile and args.reconcile.exists():
        reconcile_against = json.loads(args.reconcile.read_text(encoding="utf-8"))
    synced = sync_alerts(
        result,
        repository=args.repository,
        token=os.environ.get("GITHUB_TOKEN", ""),
        composer_status=args.composer_status,
        reconcile_against=reconcile_against,
    )
    print(json.dumps(synced, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
