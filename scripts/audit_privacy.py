from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", "node_modules", "dist", ".astro", "__pycache__"}
TEXT_SUFFIXES = {
    ".astro",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".ts",
    ".txt",
    ".yml",
    ".yaml",
}
PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_token": re.compile(r"\b(?:ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    "wechat_secret": re.compile(r"(?i)(?:appsecret|wechat_app_secret)\s*[:=]\s*['\"]?[A-Za-z0-9]{16,}"),
    "windows_user_path": re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+\\"),
}


def main() -> int:
    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: {label}")
    if findings:
        print("\n".join(findings))
        return 1
    print("privacy audit: 0 findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
