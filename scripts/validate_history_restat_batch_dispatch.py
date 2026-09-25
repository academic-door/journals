from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


CONTROL_ISSUE = 205
TRUSTED_ACTORS = {"SIMON-WORLD"}
COMMAND_RE = re.compile(r"^/history-restat-batch ([a-z0-9]+(?:-[a-z0-9]+)*)$")
ALLOWED_HOST = "direct.mit.edu"


class RestatBatchDispatchValidationError(ValueError):
    pass


def _require_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RestatBatchDispatchValidationError(f"{label} must be an object")
    return value


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RestatBatchDispatchValidationError(f"{label} is unreadable") from exc
    return _require_dict(payload, label)


def _validate_evidence(path: Path, *, issue_id: str) -> None:
    evidence = _read_json(path, f"evidence {issue_id}")
    errors: list[str] = []
    if evidence.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if evidence.get("capture_mode") != "official-roster-evidence":
        errors.append("capture_mode must be official-roster-evidence")
    if evidence.get("method") != "browser-authorized":
        errors.append("method must be browser-authorized")
    if evidence.get("finalized") is not True:
        errors.append("evidence must be finalized")
    if evidence.get("journal_id") != "restat":
        errors.append("journal_id must be restat")
    if evidence.get("issue_id") != issue_id:
        errors.append("issue_id mismatch")
    parsed = urlparse(str(evidence.get("official_url") or ""))
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        errors.append("official_url must be an HTTPS MIT Press Direct issue URL")
    items = evidence.get("items")
    if not isinstance(items, list) or not items:
        errors.append("official roster items are missing")
    else:
        sequences = [item.get("sequence") for item in items if isinstance(item, dict)]
        if sequences != list(range(1, len(items) + 1)):
            errors.append("official roster sequence must be contiguous from 1")
        if any(not str(item.get("doi") or "").startswith("10.1162/") for item in items if isinstance(item, dict)):
            errors.append("every publishable roster item must carry an MIT Press DOI")
    excluded = evidence.get("excluded_items", [])
    if not isinstance(excluded, list):
        errors.append("excluded_items must be a list")
    if evidence.get("excluded_item_count") != len(excluded):
        errors.append("excluded_item_count mismatch")
    if errors:
        raise RestatBatchDispatchValidationError(
            f"evidence {issue_id} failed validation: " + "; ".join(errors)
        )


def validate_event(event: dict[str, object], *, repo_root: Path) -> tuple[str, list[str]]:
    if event.get("action") != "created":
        raise RestatBatchDispatchValidationError("only newly created issue comments are accepted")
    issue = _require_dict(event.get("issue"), "issue")
    if issue.get("number") != CONTROL_ISSUE:
        raise RestatBatchDispatchValidationError("comment is not on the R3 control issue")
    if "pull_request" in issue:
        raise RestatBatchDispatchValidationError("pull-request comments are not accepted")
    comment = _require_dict(event.get("comment"), "comment")
    user = _require_dict(comment.get("user"), "comment.user")
    if str(user.get("login") or "") not in TRUSTED_ACTORS:
        raise RestatBatchDispatchValidationError("comment actor is not trusted")
    body = str(comment.get("body") or "").strip()
    match = COMMAND_RE.fullmatch(body)
    if match is None:
        raise RestatBatchDispatchValidationError("comment does not match the controlled RESTAT batch command")
    batch_id = match.group(1)

    manifest_path = repo_root / "data/provenance/official-evidence-batches" / f"{batch_id}.json"
    manifest = _read_json(manifest_path, "RESTAT batch manifest")
    errors: list[str] = []
    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if manifest.get("batch_id") != batch_id:
        errors.append("batch_id mismatch")
    if manifest.get("publisher_family") != "mit-press-direct":
        errors.append("publisher_family must be mit-press-direct")
    if manifest.get("journal_id") != "restat":
        errors.append("journal_id must be restat")
    if manifest.get("method") != "browser-authorized":
        errors.append("method must be browser-authorized")
    if manifest.get("finalized") is not True:
        errors.append("batch must be finalized")
    issue_ids = manifest.get("issue_ids")
    evidence_paths = manifest.get("evidence_paths")
    if not isinstance(issue_ids, list) or not issue_ids or len(issue_ids) > 20:
        errors.append("issue_ids must contain 1..20 entries")
        issue_ids = []
    if len(set(str(x) for x in issue_ids)) != len(issue_ids):
        errors.append("issue_ids must be unique")
    if not isinstance(evidence_paths, list) or len(evidence_paths) != len(issue_ids):
        errors.append("evidence_paths must align one-to-one with issue_ids")
        evidence_paths = []
    if errors:
        raise RestatBatchDispatchValidationError(
            "RESTAT batch manifest failed validation: " + "; ".join(errors)
        )

    normalized: list[str] = []
    expected_root = Path("data/provenance/official-rosters/restat")
    for raw_issue_id, raw_path in zip(issue_ids, evidence_paths, strict=True):
        issue_id = str(raw_issue_id).strip()
        if not re.fullmatch(r"restat-\d+-\d+", issue_id):
            raise RestatBatchDispatchValidationError(f"invalid RESTAT issue_id: {issue_id}")
        rel = Path(str(raw_path))
        if rel.is_absolute() or ".." in rel.parts:
            raise RestatBatchDispatchValidationError(f"unsafe evidence path: {raw_path}")
        try:
            rel.relative_to(expected_root)
        except ValueError as exc:
            raise RestatBatchDispatchValidationError(
                f"evidence path outside RESTAT official-rosters: {raw_path}"
            ) from exc
        if rel.name != f"{issue_id}.json":
            raise RestatBatchDispatchValidationError(
                f"evidence path does not match {issue_id}"
            )
        path = repo_root / rel
        if not path.is_file():
            raise RestatBatchDispatchValidationError(f"evidence missing for {issue_id}")
        _validate_evidence(path, issue_id=issue_id)
        normalized.append(issue_id)
    return batch_id, normalized


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
    except (RestatBatchDispatchValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"controlled RESTAT batch dispatch rejected: {exc}", file=sys.stderr)
        return 2
    print(f"controlled RESTAT batch dispatch accepted for {batch_id}: {len(issue_ids)} issues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
