from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


CONTROL_ISSUE = 265
TRUSTED_ACTORS = {"SIMON-WORLD"}
COMMAND_RE = re.compile(r"^/history-evidence ([a-z0-9]+(?:-[a-z0-9]+){2,})$")
ALLOWED_SCIENCEDIRECT_HOSTS = {"www.sciencedirect.com", "sciencedirect.com"}


class DispatchValidationError(ValueError):
    pass


def _require_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DispatchValidationError(f"{label} must be an object")
    return value


def validate_event(event: dict[str, object], *, repo_root: Path) -> str:
    if event.get("action") != "created":
        raise DispatchValidationError("only newly created issue comments are accepted")

    issue = _require_dict(event.get("issue"), "issue")
    if issue.get("number") != CONTROL_ISSUE:
        raise DispatchValidationError("comment is not on the designated control issue")
    if "pull_request" in issue:
        raise DispatchValidationError("pull-request comments are not accepted")

    comment = _require_dict(event.get("comment"), "comment")
    user = _require_dict(comment.get("user"), "comment.user")
    actor = str(user.get("login") or "")
    if actor not in TRUSTED_ACTORS:
        raise DispatchValidationError("comment actor is not trusted")

    body = str(comment.get("body") or "").strip()
    match = COMMAND_RE.fullmatch(body)
    if match is None:
        raise DispatchValidationError("comment does not match the controlled command shape")
    issue_id = match.group(1)

    snapshot_path = (
        repo_root
        / "data"
        / "provenance"
        / "browser-snapshots"
        / "sciencedirect"
        / f"{issue_id}.json"
    )
    if not snapshot_path.is_file():
        raise DispatchValidationError("matching browser-authorized snapshot is missing")

    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchValidationError("browser-authorized snapshot is unreadable") from exc
    snapshot = _require_dict(snapshot, "snapshot")
    if snapshot.get("issue_id") != issue_id:
        raise DispatchValidationError("snapshot issue id does not match the command")
    if not str(snapshot.get("journal_id") or "").strip():
        raise DispatchValidationError("snapshot journal id is missing")
    official_url = str(snapshot.get("official_url") or "")
    parsed = urlparse(official_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SCIENCEDIRECT_HOSTS:
        raise DispatchValidationError("snapshot is not bound to official ScienceDirect evidence")
    items = snapshot.get("items")
    if not isinstance(items, list) or not items:
        raise DispatchValidationError("snapshot item roster is missing")

    return issue_id


def write_github_output(path: Path, issue_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"issue_id={issue_id}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the bounded /history-evidence issue-comment trigger."
    )
    parser.add_argument("event_path", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    try:
        event = json.loads(args.event_path.read_text(encoding="utf-8"))
        event = _require_dict(event, "event")
        issue_id = validate_event(event, repo_root=args.repo_root.resolve())
        if args.github_output is not None:
            write_github_output(args.github_output, issue_id)
    except (OSError, json.JSONDecodeError, DispatchValidationError) as exc:
        print(f"controlled history dispatch rejected: {exc}", file=sys.stderr)
        return 2

    print(f"controlled history dispatch accepted for {issue_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
