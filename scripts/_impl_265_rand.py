from __future__ import annotations

from pathlib import Path


def patch_wiley() -> None:
    path = Path("collectors/wiley.py")
    text = path.read_text(encoding="utf-8")
    if "def parse_latest_issue_signal(" in text:
        return
    marker = "\ndef _parse_article_page(content: bytes, source_url: str) -> dict[str, Any]:\n"
    if marker not in text:
        raise SystemExit("wiley insertion marker missing")
    implementation = r'''

def parse_latest_issue_signal(content: bytes, source_url: str) -> dict[str, str]:
    """Parse Wiley's first-party Recent issues block as issue-existence evidence."""

    if not _is_official_wiley_url(source_url):
        raise ValueError("Wiley issue signals require an official HTTPS Wiley URL")

    soup = BeautifulSoup(content, "html.parser")
    recent_heading = next(
        (
            node
            for node in soup.find_all(["h2", "h3", "h4"])
            if _normalise_section(_text(node)) == "recent issues"
        ),
        None,
    )
    if not isinstance(recent_heading, Tag):
        raise ValueError("Wiley Recent issues block is missing")

    scope = recent_heading.find_parent(["section", "article"])
    links: list[Tag] = []
    if isinstance(scope, Tag):
        links = [
            link for link in scope.select("a[href*='/toc/']") if isinstance(link, Tag)
        ]
    else:
        for sibling in recent_heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {"h2", "h3", "h4"}:
                break
            if not isinstance(sibling, Tag):
                continue
            if sibling.name == "a" and "/toc/" in str(sibling.get("href", "")):
                links.append(sibling)
            links.extend(
                link
                for link in sibling.select("a[href*='/toc/']")
                if isinstance(link, Tag)
            )

    period_pattern = re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December|Winter|Spring|Summer|Fall|"
        r"Autumn(?:\s+\(Fall\))?)\s+\d{4}\b",
        re.IGNORECASE,
    )
    path_pattern = re.compile(
        r"/toc/[^/]+/(?P<year>\d{4})/(?P<volume>[^/]+)/(?P<issue>[^/?#]+)$"
    )
    candidates: list[tuple[tuple[int, int, int], dict[str, str]]] = []
    seen_urls: set[str] = set()
    for link in links:
        label = _text(link)
        match = None
        for pattern in VOLUME_ISSUE_PATTERNS:
            match = pattern.search(label)
            if match:
                break
        if not match:
            continue
        absolute = urljoin(source_url, str(link.get("href", ""))).split("#", 1)[0]
        if absolute in seen_urls or not _is_official_wiley_url(absolute):
            continue
        seen_urls.add(absolute)
        path_match = path_pattern.search(urlparse(absolute).path.rstrip("/"))
        if not path_match:
            continue
        volume = match.group("volume").strip()
        issue = match.group("issue").strip()
        if volume != path_match.group("volume") or issue != path_match.group("issue"):
            continue
        context = link.find_parent(["li", "article"]) or link.parent
        period_match = period_pattern.search(
            _text(context) if isinstance(context, Tag) else label
        )
        publication_date = period_match.group(0) if period_match else ""
        issue_number = re.search(r"\d+", issue)
        candidates.append(
            (
                (
                    int(path_match.group("year")),
                    int(volume) if volume.isdigit() else -1,
                    int(issue_number.group(0)) if issue_number else -1,
                ),
                {
                    "volume": volume,
                    "issue": issue,
                    "publication_date": publication_date,
                    "source_kind": "official_archive",
                    "source_url": absolute,
                },
            )
        )

    if not candidates:
        raise ValueError("Wiley Recent issues block exposed no auditable issue identity")
    return max(candidates, key=lambda item: item[0])[1]
'''
    path.write_text(text.replace(marker, implementation + marker, 1), encoding="utf-8")


def patch_monitor() -> None:
    path = Path("scripts/journal_monitor.py")
    text = path.read_text(encoding="utf-8")

    if "from collectors.wiley import parse_latest_issue_signal" not in text:
        marker = "import yaml\n"
        if marker not in text:
            raise SystemExit("monitor import marker missing")
        text = text.replace(
            marker,
            marker + "\nfrom collectors.wiley import parse_latest_issue_signal\n",
            1,
        )

    candidate_marker = "\ndef _candidate_payload(candidate: Candidate) -> dict[str, Any]:\n"
    if "def fetch_official_issue_signal(" not in text:
        if candidate_marker not in text:
            raise SystemExit("candidate payload marker missing")
        signal_code = r'''

def fetch_official_issue_signal(
    config: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any] | None:
    """Fetch configured first-party issue-existence evidence, fail closed."""

    if config.get("announcement_source") != "wiley_recent_issues":
        return None
    source_url = str(config.get("announcement_url", "")).strip()
    if not source_url:
        return None
    client = session or requests.Session()
    client.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
    response = _request_with_retry(client, source_url, attempts=2, timeout=30)
    return parse_latest_issue_signal(response.content, source_url)


def _issue_signal_matches_candidate(
    candidate: Candidate | None,
    signal: dict[str, Any] | None,
) -> bool:
    if not candidate or not signal:
        return False
    return (
        candidate.volume.strip() == str(signal.get("volume", "")).strip()
        and candidate.issue.strip() == str(signal.get("issue", "")).strip()
        and str(signal.get("source_kind", "")) == "official_archive"
        and str(signal.get("source_url", "")).startswith(
            "https://onlinelibrary.wiley.com/"
        )
    )


def _official_issue_signal_announcement(
    journal_id: str,
    candidate: Candidate | None,
    signal: dict[str, Any] | None,
    observed_at: str,
) -> dict[str, Any] | None:
    if not _issue_signal_matches_candidate(candidate, signal):
        return None
    assert candidate is not None and signal is not None
    volume = candidate.volume.strip()
    issue = candidate.issue.strip()
    publication_date = str(signal.get("publication_date", "")).strip()
    if not volume or not issue or not publication_date:
        return None
    token = lambda value: re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    volume_token = token(volume)
    issue_token = token(issue)
    if not volume_token or not issue_token:
        return None
    return {
        "schema_version": "1.0",
        "journal_id": journal_id,
        "issue_id": f"{journal_id}-{volume_token}-{issue_token}",
        "volume": volume,
        "issue": issue,
        "issue_label": f"Vol. {volume} · No. {issue}",
        "publication_date": publication_date,
        "publication_state": "announced",
        "source_authority": "first_party",
        "source_kind": "official_archive",
        "source_url": str(signal["source_url"]),
        "observed_at": observed_at,
    }
'''
        text = text.replace(candidate_marker, signal_code + candidate_marker, 1)

    replacements = [
        (
            '''def evaluate_observation(\n    candidate: Candidate | None,\n    baseline: dict[str, Any],\n    previous_entry: dict[str, Any],\n    *,\n    rss_dois: set[str] | None = None,\n) -> tuple[str, dict[str, Any]]:''',
            '''def evaluate_observation(\n    candidate: Candidate | None,\n    baseline: dict[str, Any],\n    previous_entry: dict[str, Any],\n    *,\n    rss_dois: set[str] | None = None,\n    official_issue_match: bool = False,\n) -> tuple[str, dict[str, Any]]:''',
        ),
        (
            '''    evidence = ["crossref"]\n    if rss_dois and set(candidate.unseen_dois) & rss_dois:\n        evidence.append("official_rss")\n''',
            '''    evidence = ["crossref"]\n    if rss_dois and set(candidate.unseen_dois) & rss_dois:\n        evidence.append("official_rss")\n    if official_issue_match:\n        evidence.append("official_archive")\n''',
        ),
        (
            '''    confirmed = (\n        "official_rss" in evidence\n        or same_issue\n        or clearly_new_issue\n        or seen_count >= 2\n    )''',
            '''    confirmed = (\n        "official_rss" in evidence\n        or "official_archive" in evidence\n        or same_issue\n        or clearly_new_issue\n        or seen_count >= 2\n    )''',
        ),
        (
            '''def detect_all(\n    journal_configs: dict[str, dict[str, Any]],\n    state: dict[str, Any],\n    *,\n    crossref_fetcher: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]] = fetch_crossref_items,\n    rss_fetcher: Callable[[str], set[str]] = fetch_rss_dois,\n) -> tuple[dict[str, Any], dict[str, Any]]:''',
            '''def detect_all(\n    journal_configs: dict[str, dict[str, Any]],\n    state: dict[str, Any],\n    *,\n    crossref_fetcher: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]] = fetch_crossref_items,\n    rss_fetcher: Callable[[str], set[str]] = fetch_rss_dois,\n    issue_signal_fetcher: Callable[[dict[str, Any]], dict[str, Any] | None] = fetch_official_issue_signal,\n) -> tuple[dict[str, Any], dict[str, Any]]:''',
        ),
        (
            '''                status, observation = evaluate_observation(\n                    candidate,\n                    baseline,\n                    previous,\n                    rss_dois=rss_dois,\n                )''',
            '''                issue_signal: dict[str, Any] | None = None\n                official_issue_match = False\n                if candidate and config.get("announcement_source"):\n                    try:\n                        issue_signal = issue_signal_fetcher(config)\n                        official_issue_match = _issue_signal_matches_candidate(\n                            candidate, issue_signal\n                        )\n                    except Exception:\n                        # First-party signals are additive only; source blocking\n                        # must not break metadata detection or manufacture authority.\n                        issue_signal = None\n                        official_issue_match = False\n                status, observation = evaluate_observation(\n                    candidate,\n                    baseline,\n                    previous,\n                    rss_dois=rss_dois,\n                    official_issue_match=official_issue_match,\n                )''',
        ),
        (
            '''                if "official_rss" in observation.get("evidence", []):\n                    announcement = _official_rss_announcement(\n                        config["id"],\n                        candidate,\n                        str(config.get("rss_url", "")),\n                        checked_at,\n                    )\n                    if announcement:\n                        entry["announcement"] = announcement\n''',
            '''                if "official_rss" in observation.get("evidence", []):\n                    announcement = _official_rss_announcement(\n                        config["id"],\n                        candidate,\n                        str(config.get("rss_url", "")),\n                        checked_at,\n                    )\n                    if announcement:\n                        entry["announcement"] = announcement\n                if "official_archive" in observation.get("evidence", []):\n                    announcement = _official_issue_signal_announcement(\n                        config["id"],\n                        candidate,\n                        issue_signal,\n                        checked_at,\n                    )\n                    if announcement:\n                        entry["announcement"] = announcement\n''',
        ),
    ]
    for old, new in replacements:
        if new in text:
            continue
        if old not in text:
            raise SystemExit(f"monitor replacement marker missing: {old[:80]!r}")
        text = text.replace(old, new, 1)

    required = [
        "from collectors.wiley import parse_latest_issue_signal",
        "def fetch_official_issue_signal(",
        "official_issue_match: bool = False",
        "issue_signal_fetcher:",
        '"official_archive" in evidence',
        "_official_issue_signal_announcement(",
    ]
    missing = [value for value in required if value not in text]
    if missing:
        raise SystemExit(f"monitor patch incomplete: {missing}")
    path.write_text(text, encoding="utf-8")


def patch_config() -> None:
    path = Path("config/journals.yml")
    text = path.read_text(encoding="utf-8")
    start = text.index("  RAND:\n")
    end = text.index("\n  AEJMICRO:\n", start)
    block = text[start:end]
    if "announcement_source: wiley_recent_issues" in block:
        return
    current = "    current_issue_url: https://onlinelibrary.wiley.com/toc/17562171/current\n"
    replacement = (
        current
        + "    announcement_source: wiley_recent_issues\n"
        + "    announcement_url: https://onlinelibrary.wiley.com/journal/17562171\n"
    )
    if current not in block:
        raise SystemExit("RAND current URL marker missing")
    block = block.replace(current, replacement, 1)
    path.write_text(text[:start] + block + text[end:], encoding="utf-8")


if __name__ == "__main__":
    patch_wiley()
    patch_monitor()
    patch_config()
