"""Build historical archives from authorized ScienceDirect rosters and Elsevier metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ElementTree
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.metadata_fallback import (
    ELSEVIER_ARTICLE_API,
    ELSEVIER_SEARCH_API,
    _clean_markup,
    _elsevier_text,
    _elsevier_lookup,
)
from scripts.import_browser_authorized_snapshot import (
    build_candidate,
    now_iso,
    translate_candidate,
    write_json,
)
from scripts.import_official_roster_evidence import reconcile_state_files
from scripts.update_journals import (
    TRANSLATION_CACHE,
    archive_issue,
    is_archivable_snapshot,
    public_issue_path,
    validate_issue,
)

PII_RE = re.compile(r"/pii/([A-Z0-9]+)", re.IGNORECASE)
PUBLISHABLE_RE = re.compile(
    r"Research article|Review article|Short communication|"
    r"Full length article|Data article|Discussion",
    re.IGNORECASE,
)
VOLUME_RE = re.compile(r"/vol/([^/]+)", re.IGNORECASE)


def comparable_title(value: object) -> str:
    """Normalize publisher-only MathML presentation differences."""

    text = unicodedata.normalize("NFKC", _clean_markup(str(value or "")))
    # ScienceDirect/Elsevier occasionally inserts directional marks or soft
    # hyphens that are invisible in the rendered title but survive API JSON.
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    text = text.translate(str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"}))
    text = " ".join(text.split()).casefold()
    # ScienceDirect can expose CO2 both as literal text plus a MathML subscript,
    # while Elsevier metadata exposes the same token as ``CO 2``.
    text = re.sub(r"\bco\s*2(?:\s+2)?\b", "co2", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpm\s*2\s*[.]\s*5(?:\s+2\s*[.]\s*5)?\b", "pm2.5", text, flags=re.IGNORECASE)
    return text


CONFUSABLE_TITLE_CHARS = str.maketrans({'ꜱ': 's', 'ſ': 's', 'ϲ': 'c', 'Ϲ': 'C', 'ϵ': 'e', 'ℊ': 'g', 'ɡ': 'g', 'ɑ': 'a', 'ɩ': 'i', 'ʟ': 'l', 'ᴍ': 'm', 'ᴏ': 'o', 'ᴘ': 'p', 'ʀ': 'r', 'ᴛ': 't', 'ᴜ': 'u', 'ᴠ': 'v', 'ᴡ': 'w', 'ʏ': 'y', 'ℎ': 'h', 'ℓ': 'l', 'ℕ': 'N', 'ℙ': 'P', 'ℝ': 'R', 'ℤ': 'Z', 'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x', 'у': 'y', 'ѕ': 's', 'і': 'i', 'ј': 'j', 'Α': 'A', 'Β': 'B', 'Ε': 'E', 'Ζ': 'Z', 'Η': 'H', 'Ι': 'I', 'Κ': 'K', 'Μ': 'M', 'Ν': 'N', 'Ο': 'O', 'Ρ': 'P', 'Τ': 'T', 'Υ': 'Y', 'Χ': 'X', 'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ι': 'i', 'κ': 'k', 'μ': 'm', 'ν': 'n', 'ο': 'o', 'ρ': 'p', 'τ': 't', 'υ': 'y', 'χ': 'x', 'σ': 's', 'ς': 's'})

def loose_comparable_title(value: object) -> str:
    text = unicodedata.normalize('NFKD', comparable_title(value)).translate(CONFUSABLE_TITLE_CHARS)
    return ''.join(char for char in text if char.isalnum())


def metadata_title_for_compare(value: object) -> str:
    """Remove known Elsevier metadata notes appended to article titles."""

    text = str(value or "")
    text = re.split(
        r"\b(?:funding information|acknowledg(?:e)?ments?|declaration of interest|"
        r"credit authorship contribution statement)\s*:",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    # Some Article API responses place a standalone affiliation/reference
    # marker between the title and the metadata note (for example ``1``
    # before ``Funding information``).  It is presentation metadata, not
    # part of the article title.  Restrict this to trailing digit-only
    # lines so legitimate numeric titles remain unchanged.
    text = re.sub(r"(?:\s+\d+)+\s*$", "", text)
    return text


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def pii_from_href(value: object) -> str:
    match = PII_RE.search(str(value or ""))
    return match.group(1).upper() if match else ""


def raw_type(box_text: str) -> str:
    match = re.search(
        r"(Research article|Review article|Short communication|Full length article|"
        r"Data article|Discussion|Editorial|Erratum|Corrigendum|Correction)",
        box_text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return "Research article" if PUBLISHABLE_RE.search(box_text) else "Editorial"


def volume_from_roster(roster: dict[str, Any]) -> str:
    explicit = str(roster.get("volume", "")).strip()
    if explicit:
        return explicit
    match = VOLUME_RE.search(str(roster.get("official_url", "")))
    if match:
        return match.group(1)
    issue_id = str(roster.get("issue_id", ""))
    parts = issue_id.split("-")
    if len(parts) >= 3 and parts[-2]:
        return parts[-2]
    raise ValueError(f"cannot determine volume for {issue_id}")


def fetch_issue_metadata(
    session: requests.Session,
    piis: list[str],
    *,
    timeout: int,
) -> dict[str, dict[str, Any]]:
    headers = {
        "Accept": "application/xml",
        "X-ELS-APIKey": os.getenv("ELSEVIER_API_KEY", "").strip(),
    }
    inst_token = os.getenv("ELSEVIER_INST_TOKEN", "").strip()
    if inst_token:
        headers["X-ELS-Insttoken"] = inst_token
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "dc": "http://purl.org/dc/elements/1.1/",
        "prism": "http://prismstandard.org/namespaces/basic/2.0/",
        "sci": "http://www.elsevier.com/xml/schemas/sciencedirect",
    }
    def fetch_one(pii: str) -> tuple[str, dict[str, Any]]:
        """Fetch one official PII with a bounded retry budget.

        The old serial loop could spend hours behind one stalled API request.
        Keep the official PII lookup authoritative, but bound each request and
        use a small worker pool so a single bad article cannot block an issue.
        """
        worker_session = requests.Session()
        response = None
        for attempt in range(3):
            response = worker_session.get(
                f"{ELSEVIER_ARTICLE_API}/pii/{pii}",
                params={"view": "META_ABS", "httpAccept": "application/xml"},
                headers=headers,
                timeout=min(timeout, 30),
            )
            if response.status_code != 429:
                break
            retry_after = response.headers.get("Retry-After", "5")
            try:
                delay = min(30, max(2, int(retry_after)))
            except ValueError:
                delay = 5
            time.sleep(min(20, delay * (attempt + 1)))
        assert response is not None
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        found = False
        entries = root.findall("atom:entry", ns)
        # The direct Article API may return the article coredata element as
        # the XML root instead of wrapping it in an Atom entry.
        if not entries:
            entries = [root]
        for entry in entries:
            entry_pii = re.sub(
                r"[^A-Za-z0-9]",
                "",
                entry.findtext("pii", "", ns).strip()
                or entry.findtext("sci:pii", "", ns).strip(),
            ).upper()
            if entry is not root and entry_pii != pii.upper():
                continue
            if entry is root and not entry_pii:
                entry_pii = pii.upper()
            if entry_pii != pii.upper():
                continue
            found = True
            doi = entry.findtext(".//prism:doi", "", ns).strip().lower()
            abstract = _elsevier_text(root, {"description", "abstract"})
            creators = [
                node.text.strip()
                for node in root.findall(".//dc:creator", ns)
                if node.text and node.text.strip()
            ]
            if not creators:
                creators = [
                    node.text.strip()
                    for node in root.findall(".//{*}indexed-name")
                    if node.text and node.text.strip()
                ]
            metadata = {
                "doi": doi,
                "title_en": entry.findtext(".//dc:title", "", ns).strip(),
                "authors": creators,
                "abstract_en": abstract,
            }
            break
        if not found:
            raise ValueError(f"Elsevier metadata returned no article for PII {pii}")
        return pii.upper(), metadata

    output: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    # The pool is deliberately small to stay below shared Elsevier rate limits
    # while avoiding a many-hour serial queue for a large historical issue.
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="elsevier") as pool:
        futures = {pool.submit(fetch_one, pii): pii for pii in piis}
        for future in as_completed(futures):
            pii = futures[future]
            try:
                key, metadata = future.result()
            except Exception as exc:  # retain the exact article-level blocker
                failures[pii.upper()] = f"{type(exc).__name__}: {exc}"
            else:
                output[key] = metadata
    if failures:
        details = "; ".join(f"{pii} ({reason})" for pii, reason in sorted(failures.items()))
        raise ValueError(f"Elsevier metadata fetch failures: {details}")
    for pii in piis:
        metadata = output[pii.upper()]
        if not metadata["abstract_en"]:
            time.sleep(0.35)
            lookup = _elsevier_lookup(
                session,
                pii,
                doi=str(metadata.get("doi", "")),
                timeout=timeout,
            )
            metadata["abstract_en"] = str(lookup.get("abstract", "")).strip()
    return output


def build_rich_snapshot(
    roster: dict[str, Any],
    *,
    session: requests.Session,
    journal: dict[str, Any],
) -> dict[str, Any]:
    all_roster_items = list(roster.get("items", []))
    # Publisher front matter and corrections are retained in the original
    # browser roster for later exclusion evidence, but their PIIs are not
    # searchable as research metadata in the Elsevier API.
    roster_items = [
        item
        for item in all_roster_items
        if PUBLISHABLE_RE.search(str(item.get("box_text", "")))
    ]
    piis = [pii_from_href(item.get("href")) for item in roster_items]
    by_pii = fetch_issue_metadata(session, piis, timeout=90)

    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for order, item in enumerate(roster_items, start=1):
        pii = pii_from_href(item.get("href"))
        metadata = by_pii.get(pii)
        if metadata is None:
            missing.append(pii or str(item.get("title", "")))
            continue
        title = str(item.get("title", "")).strip()
        api_title = str(metadata.get("title_en", "")).strip()
        normalized_title = comparable_title(title)
        normalized_api_title = comparable_title(metadata_title_for_compare(api_title))
        loose_title = loose_comparable_title(title)
        loose_api_title = loose_comparable_title(metadata_title_for_compare(api_title))
        hamming_match = (
            len(loose_title) >= 40
            and len(loose_api_title) >= 40
            and __import__('difflib').SequenceMatcher(None, loose_title, loose_api_title).ratio() >= 0.985
        )
        if (
            normalized_title != normalized_api_title
            and loose_title != loose_api_title
            and not hamming_match
        ):
            raise ValueError(f"official title mismatch for {pii}: {title!r} != {api_title!r}")
        items.append(
            {
                "official_order": order,
                "pii": pii,
                "doi": str(metadata.get("doi", "")).strip().lower(),
                "title_en": title,
                "raw_type": raw_type(str(item.get("box_text", ""))),
                "authors": list(metadata.get("authors", [])),
                "abstract_en": str(metadata.get("abstract_en", "")).strip(),
                "source_url": f"https://www.sciencedirect.com/science/article/pii/{pii}",
            }
        )
    if missing:
        raise ValueError(f"Elsevier metadata missing roster PIIs: {missing}")
    if len(items) != len(roster_items):
        raise ValueError("browser roster and official metadata counts differ")
    return {
        "schema_version": "1.0",
        "journal_id": str(roster["journal_id"]),
        "journal_name": str(journal["name"]),
        "volume": volume_from_roster(roster),
        "issue": str(roster.get("issue", "c")),
        "publication_date": str(roster["publication_date"]),
        "source_url": str(roster["official_url"]),
        "captured_at": str(roster.get("captured_at") or now_iso()),
        "capture_mode": "browser-authorized",
        "institutional_access_confirmed": True,
        "items": items,
    }


def promote_historical(candidate: dict[str, Any], *, state_root: Path) -> Path:
    if not is_archivable_snapshot(candidate):
        raise ValueError(
            f"publication gate failed for {candidate['issue_id']}: "
            f"{candidate['quality'].get('translation_complete', 0)}/"
            f"{candidate['research_article_count']} translated"
        )
    target = public_issue_path(str(candidate["journal_id"]))
    target = target.parent / "issues" / f"{candidate['issue_id']}.json"
    write_json(target, candidate)
    archive_issue(candidate)
    reconcile_state_files(candidate, state_root)
    readback = read_json(target)
    if readback.get("issue_id") != candidate.get("issue_id"):
        raise RuntimeError(f"historical archive read-back failed: {target}")
    return target


def load_journal(journal_id: str) -> dict[str, Any]:
    config = yaml.safe_load((ROOT / "config" / "journals.yml").read_text(encoding="utf-8"))
    for journal in config["journals"].values():
        if str(journal.get("id")) == journal_id:
            return journal
    raise KeyError(f"journal not configured: {journal_id}")


def process(path: Path, *, state_root: Path, cache_root: Path, translate: bool) -> dict[str, Any]:
    roster = read_json(path)
    journal = load_journal(str(roster["journal_id"]))
    session = requests.Session()
    snapshot = build_rich_snapshot(roster, session=session, journal=journal)
    candidate = build_candidate(snapshot)
    translation_report: dict[str, Any] = {}
    if translate:
        candidate, translation_report = translate_candidate(
            candidate,
            cache_root / f"{candidate['journal_id']}.json",
            session=session,
        )
    validate_issue(candidate)
    target = ""
    if candidate.get("publication_state") == "ready":
        target = str(promote_historical(candidate, state_root=state_root))
    return {
        "issue_id": candidate["issue_id"],
        "publication_state": candidate.get("publication_state"),
        "content_status": candidate.get("content_status"),
        "source_status": candidate.get("source_status"),
        "target": target,
        "translation": translation_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshots", nargs="+", type=Path)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--translation-cache-root", type=Path, default=TRANSLATION_CACHE)
    parser.add_argument("--translate", action="store_true")
    args = parser.parse_args()
    results = [
        process(
            path,
            state_root=args.state_root,
            cache_root=args.translation_cache_root,
            translate=args.translate,
        )
        for path in args.snapshots
    ]
    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
