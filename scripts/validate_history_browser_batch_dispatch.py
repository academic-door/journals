from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


CONTROL_ISSUE = 205
TRUSTED_ACTORS = {"SIMON-WORLD"}
COMMAND_RE = re.compile(r"^/history-browser-batch ([a-z0-9]+(?:-[a-z0-9]+)*)$")
ALLOWED_HOSTS = {
    "academic.oup.com",
    "direct.mit.edu",
    "journals.uchicago.edu",
    "le.uwpress.org",
    "onlinelibrary.wiley.com",
    "www.aeaweb.org",
    "www.cambridge.org",
    "www.sciencedirect.com",
    "link.springer.com",
}
FORBIDDEN_KEY_PARTS = {
    "authorization",
    "cookie",
    "credential",
    "localstorage",
    "password",
    "session",
    "token",
}


class BatchDispatchValidationError(ValueError):
    pass


def _require_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BatchDispatchValidationError(f"{label} must be an object")
    return value


def _forbidden_keys(value: object, prefix: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                errors.append(f"forbidden private field: {prefix}{key}")
            errors.extend(_forbidden_keys(nested, f"{prefix}{key}."))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            errors.extend(_forbidden_keys(nested, f"{prefix}{index}."))
    return errors


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchDispatchValidationError(f"{label} is unreadable") from exc
    return _require_dict(payload, label)


def _validate_snapshot(
    path: Path,
    *,
    issue_id: str,
    journal_id: str,
    transport: str,
) -> None:
    snapshot = _read_json(path, f"snapshot {issue_id}")
    errors = _forbidden_keys(snapshot)
    if snapshot.get("issue_id") != issue_id:
        errors.append("snapshot issue_id mismatch")
    if snapshot.get("journal_id") != journal_id:
        errors.append("snapshot journal_id mismatch")
    if snapshot.get("capture_mode") not in {None, transport}:
        errors.append("snapshot capture_mode mismatch")
    official_url = str(snapshot.get("official_url") or "")
    parsed = urlparse(official_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        errors.append("snapshot official_url is not an allowlisted HTTPS publisher URL")
    items = snapshot.get("items")
    if not isinstance(items, list) or not items:
        errors.append("snapshot item roster is missing")
    if errors:
        raise BatchDispatchValidationError(
            f"snapshot {issue_id} failed validation: " + "; ".join(errors)
        )


def validate_event(event: dict[str, object], *, repo_root: Path) -> tuple[str, list[str]]:
    if event.get("action") != "created":
        raise BatchDispatchValidationError("only newly created issue comments are accepted")
    issue = _require_dict(event.get("issue"), "issue")
    if issue.get("number") != CONTROL_ISSUE:
        raise BatchDispatchValidationError("comment is not on the designated R3 control issue")
    if "pull_request" in issue:
        raise BatchDispatchValidationError("pull-request comments are not accepted")
    comment = _require_dict(event.get("comment"), "comment")
    user = _require_dict(comment.get("user"), "comment.user")
    if str(user.get("login") or "") not in TRUSTED_ACTORS:
        raise BatchDispatchValidationError("comment actor is not trusted")
    body = str(comment.get("body") or "").strip()
    match = COMMAND_RE.fullmatch(body)
    if match is None:
        raise BatchDispatchValidationError("comment does not match the controlled batch command")
    batch_id = match.group(1)

    manifest_path = repo_root / "data" / "provenance" / "browser-batches" / f"{batch_id}.json"
    manifest = _read_json(manifest_path, "browser batch manifest")
    errors = _forbidden_keys(manifest)
    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if manifest.get("batch_id") != batch_id:
        errors.append("batch_id mismatch")
    if manifest.get("finalized") is not True:
        errors.append("batch must be finalized")
    if manifest.get("transport") != "browser-authorized":
        errors.append("transport must be browser-authorized")
    if manifest.get("policy_decision") != "0018":
        errors.append("policy_decision must be 0018")
    journal_id = str(manifest.get("journal_id") or "").strip()
    if not journal_id:
        errors.append("journal_id missing")
    issue_ids = manifest.get("issue_ids")
    snapshot_paths = manifest.get("snapshot_paths")
    if not isinstance(issue_ids, list) or not issue_ids or len(issue_ids) > 50:
        errors.append("issue_ids must contain 1..50 entries")
        issue_ids = []
    if len(set(str(x) for x in issue_ids)) != len(issue_ids):
        errors.append("issue_ids must be unique")
    if not isinstance(snapshot_paths, list) or len(snapshot_paths) != len(issue_ids):
        errors.append("snapshot_paths must align one-to-one with issue_ids")
        snapshot_paths = []
    if errors:
        raise BatchDispatchValidationError(
            "browser batch manifest failed validation: " + "; ".join(errors)
        )

    normalized_ids: list[str] = []
    for raw_issue_id, raw_path in zip(issue_ids, snapshot_paths, strict=True):
        issue_id = str(raw_issue_id).strip()
        if not re.fullmatch(r"[a-z0-9]+(?:-[A-Za-z0-9]+)+", issue_id):
            raise BatchDispatchValidationError(f"invalid issue_id: {issue_id}")
        rel = Path(str(raw_path))
        if rel.is_absolute() or ".." in rel.parts:
            raise BatchDispatchValidationError(f"unsafe snapshot path: {raw_path}")
        if rel.name != f"{issue_id}.json":
            raise BatchDispatchValidationError(f"snapshot path does not match {issue_id}")
        expected_root = Path("data/provenance/browser-snapshots")
        try:
            rel.relative_to(expected_root)
        except ValueError as exc:
            raise BatchDispatchValidationError(
                f"snapshot path outside browser-snapshots: {raw_path}"
            ) from exc
        path = repo_root / rel
        if not path.is_file():
            raise BatchDispatchValidationError(f"snapshot missing for {issue_id}")
        _validate_snapshot(
            path,
            issue_id=issue_id,
            journal_id=journal_id,
            transport="browser-authorized",
        )
        normalized_ids.append(issue_id)
    return batch_id, normalized_ids


def write_github_output(path: Path, batch_id: str, issue_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"batch_id={batch_id}\n")
        handle.write(f"issue_ids={','.join(issue_ids)}\n")
        handle.write(f"issue_count={len(issue_ids)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_path", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        event = _read_json(args.event_path, "event")
        batch_id, issue_ids = validate_event(event, repo_root=args.repo_root.resolve())
        if args.github_output is not None:
            write_github_output(args.github_output, batch_id, issue_ids)
    except (BatchDispatchValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"controlled browser batch dispatch rejected: {exc}", file=sys.stderr)
        return 2
    print(
        f"controlled browser batch dispatch accepted for {batch_id}: "
        f"{len(issue_ids)} issues"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
