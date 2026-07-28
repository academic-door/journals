from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "local-enrichment"


def read_issue(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise ValueError("input must be an Academic Door issue JSON file")
    return payload


def eligible_articles(issue: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        article
        for article in issue["articles"]
        if article.get("doi")
        and (not article.get("abstract_en") or not article.get("authors"))
    ]


def build_jobs(
    issue: dict[str, Any],
    output_dir: Path,
    *,
    limit: int,
) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for article in eligible_articles(issue)[:limit]:
        doi = str(article["doi"]).strip().lower()
        safe_name = doi.replace("/", "_").replace("\\", "_").replace(":", "_")
        jobs.append(
            {
                "doi": doi,
                "output": str(output_dir / f"{safe_name}.md"),
            }
        )
    return jobs


def run_jobs(
    jobs: list[dict[str, str]],
    *,
    executable: str,
    timeout: int,
) -> list[dict[str, str]]:
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        raise RuntimeError(
            "institution-authorized enrichment is local-only and is disabled in GitHub Actions"
        )
    resolved = shutil.which(executable) if not Path(executable).exists() else executable
    if not resolved:
        raise FileNotFoundError(
            f"{executable!r} was not found; install paper-fetch locally first"
        )
    results: list[dict[str, str]] = []
    for job in jobs:
        output = Path(job["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                str(resolved),
                "--query",
                job["doi"],
                "--output",
                str(output),
            ],
            cwd=ROOT,
            check=False,
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        results.append(
            {
                "doi": job["doi"],
                "status": (
                    "fetched"
                    if completed.returncode == 0 and output.exists()
                    else "failed"
                ),
                "artifact": output.name if output.exists() else "",
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run an optional local paper-fetch enrichment pass. "
            "Outputs stay under ignored data/runtime and are never auto-published."
        )
    )
    parser.add_argument("issue", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--paper-fetch", default="paper-fetch")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    issue = read_issue(args.issue)
    jobs = build_jobs(issue, args.output_dir, limit=max(args.limit, 0))
    if args.dry_run:
        report = {
            "mode": "dry-run",
            "provider": "paper-fetch",
            "jobs": [
                {"doi": job["doi"], "artifact": Path(job["output"]).name}
                for job in jobs
            ],
        }
    else:
        report = {
            "mode": "local-authorized",
            "provider": "paper-fetch",
            "results": run_jobs(
                jobs,
                executable=args.paper_fetch,
                timeout=args.timeout,
            ),
        }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "enrichment-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
