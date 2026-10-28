"""Fail-closed, read-only Crossref metadata audit for the official JAE 41(6) roster.

This is NOT a production ingestion, rights clearance, translation, or activation path.
The official Wiley issue fixture remains the authoritative DOI/order/type roster.
No abstract body is emitted by the audit result.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import re
import unicodedata
from typing import Any

CROSSREF_ENDPOINT = "https://api.crossref.org/works/"
EXPECTED_PUBLISHER = "Wiley"
EXPECTED_CONTAINER = "Journal of Applied Econometrics"
EXPECTED_VOLUME = "41"
EXPECTED_ISSUE = "6"
EXPECTED_DOIS = (
    "10.1002/jae.70067", "10.1002/jae.70061",
    "10.1002/jae.70071", "10.1002/jae.70074",
    "10.1002/jae.70076", "10.1002/jae.70078",
    "10.1002/jae.70077", "10.1002/jae.70073",
    "10.1002/jae.70075", "10.1002/jae.70069",
    "10.1002/jae.70064", "10.1002/jae.70072",
)
PUBLISHER_SECTIONS = ("research_article",) * 10 + ("replication",) * 2


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, text: str) -> None:
        self.parts.append(text)


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    # Hyphen variants are typography, not changes to paper titles.
    value = re.sub(r"[\u2010-\u2015\u2212]", "-", value)
    return " ".join(re.findall(r"\w+", value))


def _plain_abstract(html: str) -> str:
    parser = _PlainText()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def assert_official_roster(official: dict[str, Any]) -> list[dict[str, Any]]:
    rows = official.get("observed_articles", [])
    if (
        official.get("issue_id") != "jae-41-6"
        or official.get("publisher_issue_index")
        != "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6"
        or official.get("first_party_full_toc_verified") is not True
        or official.get("enabled") is not False
        or len(rows) != 12
        or tuple(row.get("doi") for row in rows) != EXPECTED_DOIS
        or tuple(row.get("publisher_section") for row in rows) != PUBLISHER_SECTIONS
        or [row.get("source_sequence") for row in rows] != list(range(1, 13))
    ):
        raise ValueError("jae_crossref_official_roster_mismatch")
    return rows


def audit_deposit(official_row: dict[str, Any], deposit: dict[str, Any]) -> dict[str, Any]:
    """Check one deposited metadata record. Never return abstract/title text."""
    doi = official_row.get("doi", "")
    if doi not in EXPECTED_DOIS:
        raise ValueError("jae_crossref_unpinned_doi")
    if (
        _normalized(deposit.get("DOI", "")) != _normalized(doi)
        or deposit.get("publisher") != EXPECTED_PUBLISHER
        or EXPECTED_CONTAINER not in deposit.get("container-title", [])
        or str(deposit.get("volume", "")) != EXPECTED_VOLUME
        or str(deposit.get("issue", "")) != EXPECTED_ISSUE
    ):
        raise ValueError("jae_crossref_identity_mismatch")
    titles = deposit.get("title", [])
    if len(titles) != 1 or _normalized(titles[0]) != _normalized(official_row["title_en"]):
        raise ValueError("jae_crossref_title_mismatch")
    authors = [
        " ".join(filter(None, [item.get("given", ""), item.get("family", "")])).strip()
        for item in deposit.get("author", [])
    ]
    expected = official_row["authors_en"]
    if len(authors) != len(expected) or any(
        _normalized(a) != _normalized(b) for a, b in zip(authors, expected)
    ):
        raise ValueError("jae_crossref_author_mismatch")
    html = deposit.get("abstract", "")
    if not isinstance(html, str):
        raise ValueError("jae_crossref_missing_abstract")
    plain = _plain_abstract(html)
    if len(plain) < 80:
        raise ValueError("jae_crossref_missing_abstract")
    licenses = [item.get("URL", "") for item in deposit.get("license", [])]
    return {
        "doi": doi,
        "metadata_source": CROSSREF_ENDPOINT + doi,
        "official_issue_source": "https://onlinelibrary.wiley.com/toc/10991255/2026/41/6",
        "title_author_volume_issue_match": True,
        "abstract_nonempty": True,
        "abstract_characters": len(plain),
        "abstract_sha256": hashlib.sha256(plain.encode("utf-8")).hexdigest(),
        "deposit_license_urls": licenses,
        "abstract_reuse_rights_verified": False,
        "production_en_ingested": False,
        "production_cn_translation_verified": False,
        "eligible_for_activation": False,
    }


def audit_issue(official: dict[str, Any], deposits: list[dict[str, Any]]) -> dict[str, Any]:
    rows = assert_official_roster(official)
    if len(deposits) != 12:
        raise ValueError("jae_crossref_incomplete_deposit_batch")
    evidence = [audit_deposit(row, metadata) for row, metadata in zip(rows, deposits)]
    return {
        "issue_id": "jae-41-6",
        "source": "publisher-deposited Crossref DOI metadata; NOT publisher issue HTML",
        "official_roster_verified": True,
        "crossref_deposit_identity_and_abstract_verified": True,
        "article_count": 12,
        "articles": evidence,
        "rights_and_translation_gate": "BLOCKED_PENDING_PER_ARTICLE_REVIEW",
        "enable": False,
    }
