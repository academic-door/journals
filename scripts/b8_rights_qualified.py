"""Fail-closed B8 rights-qualified public metadata projection.

This is NOT a production writer. A staged, fully validated source candidate may
contain licensed abstracts. Only explicitly adjudicated, compatible article
rights permit those text fields into a *separate* public projection. The default
for every unknown license is metadata only, even if Crossref exposes text.
"""
from __future__ import annotations

import copy
from urllib.parse import urlparse

MODE = "metadata_first_rights_qualified_v1"
CC_BY_4 = "https://creativecommons.org/licenses/by/4.0/"
PROJECTION_FIELDS = {
    "paper_id", "sequence", "article_type", "title_en", "title_cn",
    "authors", "abstract_en", "abstract_cn", "doi", "source_url",
    "sources", "translation", "quality_flags", "public_rights",
}
ISSUE_FIELDS = {
    "schema_version", "issue_id", "journal_id", "journal_name", "volume",
    "issue", "publication_date", "source_url", "retrieved_at",
    "expected_article_count", "research_article_count", "status",
    "articles", "quality", "content_counts", "issue_label",
}
SAFE_SOURCE_KEYS = {"issue", "roster", "metadata", "abstract_en"}


def _https_url(value: object) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username
    except ValueError:
        return False


def _rights_decision(raw: dict | None, article: dict) -> dict:
    """Reject permissive, unverifiable, non-commercial or no-derivatives labels."""
    if raw is None or raw.get("status") == "withheld":
        return {"status": "withheld", "reason": "reuse_or_derivative_rights_unverified"}
    if raw.get("status") != "verified_cc_by_4_0":
        raise ValueError("unsupported public rights decision")
    if raw.get("license_url") != CC_BY_4:
        raise ValueError("only exact verified CC BY 4.0 supported")
    if not _https_url(raw.get("evidence_url")):
        raise ValueError("missing verifiable per-article license evidence URL")
    if not str(raw.get("reviewed_by", "")).strip() or not str(
        raw.get("attribution", "")
    ).strip():
        raise ValueError("license verification and attribution missing")
    if not article.get("abstract_en") or not article.get("abstract_cn"):
        raise ValueError("cannot release absent English/Chinese source content")
    return {
        "status": "verified_cc_by_4_0",
        "license_url": CC_BY_4,
        "evidence_url": raw["evidence_url"],
        "reviewed_by": str(raw["reviewed_by"]),
        "attribution": str(raw["attribution"]),
        "adaptation_notice": "Chinese translation is an adaptation.",
    }


def project_b8_issue(
    candidate: dict, *, expected_journal_id: str, expected_issue_id: str,
    rights_by_doi: dict[str, dict] | None = None,
) -> dict:
    """Build a safe API projection; never edit a staged candidate in place.

    Caller must independently verify source-grade, original official/publisher
    serial roster and candidate CI. It must *not* write this projection to data
    before downstream schema/audit/health/Pages gates are accepted.
    """
    rights_by_doi = rights_by_doi or {}
    articles = candidate.get("articles")
    if (
        candidate.get("journal_id") != expected_journal_id
        or candidate.get("issue_id") != expected_issue_id
        or candidate.get("source_status") not in {"official_verified", "publisher_verified"}
        or candidate.get("publication_state") != "ready"
        or not isinstance(articles, list) or not articles
    ):
        raise ValueError("B8 source/issue/isolated READY prerequisite failed")
    q = candidate.get("quality", {})
    if (
        not q.get("roster_match") or not q.get("order_preserved")
        or int(q.get("duplicate_count", -1)) != 0
        or len(articles) != candidate.get("research_article_count")
        or len(articles) != candidate.get("expected_article_count")
    ):
        raise ValueError("B8 metadata roster/order/quality prerequisite failed")
    doi_list = [str(a.get("doi", "")).casefold().strip() for a in articles]
    if not all(doi_list) or len(doi_list) != len(set(doi_list)):
        raise ValueError("B8 DOI identity must be complete and unique")
    if set(rights_by_doi) - set(doi_list):
        raise ValueError("rights decision contains DOI outside accepted issue")
    if [a.get("sequence") for a in articles] != list(range(1, len(articles) + 1)):
        raise ValueError("B8 issue sequence is not contiguous")
    if any(
        not a.get("title_en") or not a.get("authors") or not _https_url(a.get("source_url"))
        or a.get("article_type") not in {"research-article", "comment", "short-communication"}
        for a in articles
    ):
        raise ValueError("B8 article metadata/type/source URL invalid")
    if not _https_url(candidate.get("source_url")):
        raise ValueError("B8 issue source URL invalid")

    public = {key: copy.deepcopy(candidate[key]) for key in ISSUE_FIELDS if key in candidate}
    public["public_mode"] = MODE
    public["content_status"] = "metadata_complete"
    public["publication_state"] = "ready"
    public["status"] = "ready"
    public_articles = []
    permitted = 0
    for article, doi in zip(articles, doi_list, strict=True):
        decision = _rights_decision(rights_by_doi.get(doi), article)
        released = decision["status"] == "verified_cc_by_4_0"
        permitted += int(released)
        public_article = {
            "paper_id": article["paper_id"],
            "sequence": article["sequence"],
            "article_type": article["article_type"],
            "title_en": article["title_en"],
            "title_cn": article.get("title_cn", "") if released else "",
            "authors": copy.deepcopy(article["authors"]),
            "abstract_en": article.get("abstract_en", "") if released else "",
            "abstract_cn": article.get("abstract_cn", "") if released else "",
            "doi": article["doi"],
            "source_url": article["source_url"],
            "sources": {
                key: copy.deepcopy(value)
                for key, value in article.get("sources", {}).items()
                if key in SAFE_SOURCE_KEYS and isinstance(value, str) and _https_url(value)
            },
            "translation": (
                {"status": "complete", "rights": "verified_cc_by_4_0"}
                if released else {"status": "rights_withheld"}
            ),
            "quality_flags": [] if released else ["rights_withheld"],
            "public_rights": decision,
        }
        if "publication_date" in article:
            public_article["publication_date"] = article["publication_date"]
        if set(public_article) - (PROJECTION_FIELDS | {"publication_date"}):
            raise ValueError("public article projection introduced unscreened fields")
        public_articles.append(public_article)
    public["articles"] = public_articles
    public["public_content"] = {
        "mode": MODE,
        "metadata_articles": len(public_articles),
        "abstracts_en_visible": permitted,
        "translations_cn_visible": permitted,
        "rights_withheld_articles": len(public_articles) - permitted,
    }
    # Do not copy old issue-level source excerpts, browser captures, or any
    # unreviewed private evidence blobs into the public issue payload.
    public["quality"] = {
        "roster_match": True,
        "order_preserved": True,
        "doi_complete": len(public_articles),
        "authors_complete": len(public_articles),
        "abstract_en_complete": permitted,
        "translation_complete": permitted,
        "duplicate_count": 0,
        "flags": [] if permitted == len(public_articles) else ["rights_qualified_metadata"],
        "roster_authority": q.get("roster_authority", ""),
        "roster_transport": q.get("roster_transport", ""),
    }
    # Exclusion evidence is retained separately as canonical source candidate;
    # no private excluded-item text can leak through this public projection.
    public.pop("content_counts", None)
    return public
