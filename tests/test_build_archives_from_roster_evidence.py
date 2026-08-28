from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_archives_from_roster_evidence import (
    _metadata_for_dois,
    build_candidate_from_evidence,
    process_evidence,
)
from scripts.translate_issue import _source_hash


class BuildArchivesFromRosterEvidenceTests(unittest.TestCase):
    def write(self, root: Path, relative: str, value: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")


    def test_front_matter_and_comments_do_not_block_archiving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_root = root / "evidence"
            api_root = root / "api"
            state_root = root / "state"
            staging_root = root / "staging"
            cache_root = root / "cache"
            for folder in (
                evidence_root,
                api_root,
                state_root,
                staging_root,
                cache_root,
            ):
                folder.mkdir(parents=True, exist_ok=True)

            evidence = {
                "schema_version": "1.0",
                "capture_mode": "official-roster-evidence",
                "method": "official-page-read",
                "captured_at": "2026-08-26T00:00:00+00:00",
                "finalized": True,
                "journal_id": "demo",
                "issue_id": "demo-1-1",
                "official_url": "https://onlinelibrary.wiley.com/toc/12345678/2024/1/1",
                "excluded_item_count": 0,
                "items": [
                    {
                        "sequence": 1,
                        "doi": "10.1111/demo.10001",
                        "title_en": "First Page",
                    },
                    {
                        "sequence": 2,
                        "doi": "10.1111/demo.10002",
                        "title_en": "A Test of Policy",
                    },
                    {
                        "sequence": 3,
                        "doi": "10.1111/demo.10003",
                        "title_en": "Another Empirical Study",
                    },
                    {
                        "sequence": 4,
                        "doi": "10.1111/demo.10004",
                        "title_en": "Tax Smoothing in Frictional Labor Markets: A Reply",
                    },
                ],
            }
            self.write(
                evidence_root,
                "demo/demo-1-1.json",
                json.dumps(evidence),
            )
            journals_path = root / "journals.yml"
            journals_path.write_text(
                "journals:\n"
                "  DEMO:\n"
                "    id: demo\n"
                "    name: Demo Journal\n"
                "    issn: 1234-5678\n"
                "    collector: wiley\n"
                "    enabled: true\n",
                encoding="utf-8",
            )
            crossref_items = [
                {
                    "DOI": "10.1111/demo.10002",
                    "title": ["A Test of Policy"],
                    "author": [{"given": "Alice", "family": "Smith"}],
                    "abstract": "<jats:p>We study the policy effect.</jats:p>",
                    "issued": {"date-parts": [[2024, 1]]},
                },
                {
                    "DOI": "10.1111/demo.10003",
                    "title": ["Another Empirical Study"],
                    "author": [{"given": "Carol", "family": "Lee"}],
                    "abstract": "<jats:p>We estimate the empirical effect.</jats:p>",
                    "issued": {"date-parts": [[2024, 1]]},
                },
                {
                    "DOI": "10.1111/demo.10004",
                    "title": ["Comment on a Paper"],
                    "author": [{"given": "Dan", "family": "Kim"}],
                    "issued": {"date-parts": [[2024, 1]]},
                },
            ]
            article2 = {
                "doi": "10.1111/demo.10002",
                "title_en": "A Test of Policy",
                "abstract_en": "We study the policy effect.",
            }
            article3 = {
                "doi": "10.1111/demo.10003",
                "title_en": "Another Empirical Study",
                "abstract_en": "We estimate the empirical effect.",
            }
            article4 = {
                "doi": "10.1111/demo.10004",
                "title_en": "Tax Smoothing in Frictional Labor Markets: A Reply",
                "abstract_en": "",
            }
            cache = {}
            for article, title_cn, abstract_cn in (
                (
                    article2,
                    "政策检验",
                    "我们研究了政策对经济行为的影响，并利用详细的微观数据估计了政策效应的大小与方向。",
                ),
                (
                    article3,
                    "另一项实证研究",
                    "本文基于新的数据集，采用严格的实证方法估计了关键参数，并讨论了稳健性与政策含义。",
                ),
                (article4, "评论", ""),
            ):
                cache[article["doi"]] = {
                    "title_cn": title_cn,
                    "abstract_cn": abstract_cn,
                    "source_hash": _source_hash(article),
                    "translation": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "prompt_version": "academic-door-abstract-zh-v2",
                        "translated_at": "2026-08-26T00:00:00+00:00",
                    },
                }
            self.write(cache_root, "demo.json", json.dumps(cache))

            def fake_semantic(session, dois, *, timeout):
                return {}

            with (
                patch(
                    "scripts.build_archives_from_roster_evidence._crossref_items",
                    return_value=crossref_items,
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence._semantic_scholar_metadata_batch",
                    side_effect=fake_semantic,
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence.JOURNALS_PATH",
                    journals_path,
                ),
            ):
                import requests

                session = requests.Session()
                result = process_evidence(
                    evidence_root / "demo" / "demo-1-1.json",
                    {"id": "demo", "name": "Demo Journal", "issn": "1234-5678"},
                    session=session,
                    api_root=api_root,
                    state_root=state_root,
                    staging_root=staging_root,
                    translation_cache_root=cache_root,
                    max_translations=120,
                    start_year=2022,
                    timeout=10,
                )

            self.assertEqual("ready", result["result"], result)
            archive = json.loads(
                (
                    api_root / "journals" / "demo" / "issues" / "demo-1-1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("ready", archive["publication_state"])
            self.assertEqual(3, archive["research_article_count"])
            self.assertEqual(3, archive["quality"]["translation_complete"])
            self.assertEqual(
                ["First Page"],
                [item["title_en"] for item in archive["quality"]["excluded_items"]],
            )
            reply = next(
                article
                for article in archive["articles"]
                if article["doi"] == "10.1111/demo.10004"
            )
            self.assertEqual("comment", reply["article_type"])

    def test_metadata_backfills_truncated_crossref_with_direct_and_openalex(self) -> None:
        import requests

        session = requests.Session()
        truncated = {
            "10.1111/demo.10001": {
                "authors": ["Alice Smith"],
                "abstract": "We study the first paper.",
                "title": "Paper One",
                "year": "2024",
            }
        }
        direct = {
            "authors": ["Bob Jones"],
            "abstract": "We study the second paper.",
            "title": "Paper Two",
            "year": "2024",
        }

        with (
            patch(
                "scripts.build_archives_from_roster_evidence._crossref_direct",
                return_value=direct,
            ) as crossref_direct,
            patch(
                "scripts.build_archives_from_roster_evidence._semantic_scholar_metadata_batch",
                return_value={},
            ),
            patch(
                "scripts.build_archives_from_roster_evidence._openalex_metadata",
                return_value=(["Bob Jones"], "We study the second paper.", ""),
            ),
        ):
            metadata = _metadata_for_dois(
                session,
                ["10.1111/demo.10001", "10.1111/demo.10002"],
                truncated,
                timeout=10,
            )

        self.assertEqual(["Alice Smith"], metadata["10.1111/demo.10001"]["authors"])
        self.assertEqual(
            ["Bob Jones"], metadata["10.1111/demo.10002"]["authors"]
        )
        self.assertEqual(
            "We study the second paper.",
            metadata["10.1111/demo.10002"]["abstract"],
        )
        crossref_direct.assert_called_once()

    def test_publication_date_falls_back_to_metadata_month(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_root = root / "evidence"
            api_root = root / "api"
            state_root = root / "state"
            staging_root = root / "staging"
            cache_root = root / "cache"
            for folder in (
                evidence_root,
                api_root,
                state_root,
                staging_root,
                cache_root,
            ):
                folder.mkdir(parents=True, exist_ok=True)
            evidence = {
                "schema_version": "1.0",
                "capture_mode": "official-roster-evidence",
                "method": "official-page-read",
                "captured_at": "2026-08-26T00:00:00+00:00",
                "finalized": True,
                "journal_id": "demo",
                "issue_id": "demo-5-2",
                "official_url": "https://onlinelibrary.wiley.com/toc/12345678/2024/5/2",
                "excluded_item_count": 0,
                "items": [
                    {
                        "sequence": 1,
                        "doi": "10.1111/demo.10001",
                        "title_en": "A Test of Policy",
                    }
                ],
            }
            self.write(
                evidence_root,
                "demo/demo-5-2.json",
                json.dumps(evidence),
            )
            journals_path = root / "journals.yml"
            journals_path.write_text(
                "journals:\n"
                "  DEMO:\n"
                "    id: demo\n"
                "    name: Demo Journal\n"
                "    issn: 1234-5678\n"
                "    collector: wiley\n"
                "    enabled: true\n",
                encoding="utf-8",
            )
            crossref_items = [
                {
                    "DOI": "10.1111/demo.10001",
                    "title": ["A Test of Policy"],
                    "author": [{"given": "Alice", "family": "Smith"}],
                    "abstract": "<jats:p>We study the policy effect.</jats:p>",
                    "issued": {"date-parts": [[2024, 5]]},
                }
            ]
            article = {
                "doi": "10.1111/demo.10001",
                "title_en": "A Test of Policy",
                "abstract_en": "We study the policy effect.",
            }
            cache = {
                "10.1111/demo.10001": {
                    "title_cn": "政策检验",
                    "abstract_cn": "我们研究了政策对经济行为的影响，并利用详细的微观数据估计了政策效应的大小与方向。",
                    "source_hash": _source_hash(article),
                    "translation": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "prompt_version": "academic-door-abstract-zh-v2",
                        "translated_at": "2026-08-26T00:00:00+00:00",
                    },
                }
            }
            self.write(cache_root, "demo.json", json.dumps(cache))
            with (
                patch(
                    "scripts.build_archives_from_roster_evidence._crossref_items",
                    return_value=crossref_items,
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence._semantic_scholar_metadata_batch",
                    return_value={},
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence._publication_date",
                    return_value="",
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence.JOURNALS_PATH",
                    journals_path,
                ),
            ):
                import requests

                session = requests.Session()
                result = process_evidence(
                    evidence_root / "demo" / "demo-5-2.json",
                    {"id": "demo", "name": "Demo Journal", "issn": "1234-5678"},
                    session=session,
                    api_root=api_root,
                    state_root=state_root,
                    staging_root=staging_root,
                    translation_cache_root=cache_root,
                    max_translations=120,
                    start_year=2022,
                    timeout=10,
                )
            self.assertEqual("ready", result["result"], result)
            archive = json.loads(
                (
                    api_root / "journals" / "demo" / "issues" / "demo-5-2.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("May 2024", archive["publication_date"])

    def test_builds_and_archives_issue_from_official_roster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_root = root / "evidence"
            api_root = root / "api"
            state_root = root / "state"
            staging_root = root / "staging"
            cache_root = root / "cache"
            for folder in (evidence_root, api_root, state_root, staging_root, cache_root):
                folder.mkdir(parents=True, exist_ok=True)

            evidence = {
                "schema_version": "1.0",
                "capture_mode": "official-roster-evidence",
                "method": "official-page-read",
                "captured_at": "2026-08-26T00:00:00+00:00",
                "finalized": True,
                "journal_id": "demo",
                "issue_id": "demo-1-1",
                "official_url": "https://onlinelibrary.wiley.com/toc/12345678/2024/1/1",
                "excluded_item_count": 0,
                "items": [
                    {
                        "sequence": 1,
                        "doi": "10.1111/demo.10001",
                        "title_en": "A Test of Policy",
                    },
                    {
                        "sequence": 2,
                        "doi": "10.1111/demo.10002",
                        "title_en": "Another Empirical Study",
                    },
                ],
            }
            self.write(
                evidence_root,
                "demo/demo-1-1.json",
                json.dumps(evidence),
            )

            journals = {
                "journals": {
                    "DEMO": {
                        "id": "demo",
                        "name": "Demo Journal",
                        "issn": "1234-5678",
                        "collector": "wiley",
                        "enabled": True,
                    }
                }
            }
            journals_path = root / "journals.yml"
            journals_path.write_text(
                yaml_text := "journals:\n  DEMO:\n    id: demo\n    name: Demo Journal\n    issn: 1234-5678\n    collector: wiley\n    enabled: true\n",
                encoding="utf-8",
            )

            crossref_items = [
                {
                    "DOI": "10.1111/demo.10001",
                    "title": ["A Test of Policy"],
                    "author": [
                        {"given": "Alice", "family": "Smith"},
                        {"given": "Bob", "family": "Jones"},
                    ],
                    "abstract": "<jats:p>We study the policy effect.</jats:p>",
                    "volume": "1",
                    "issue": "1",
                    "issued": {"date-parts": [[2024, 1]]},
                },
                {
                    "DOI": "10.1111/demo.10002",
                    "title": ["Another Empirical Study"],
                    "author": [{"given": "Carol", "family": "Lee"}],
                    "volume": "1",
                    "issue": "1",
                    "issued": {"date-parts": [[2024, 1]]},
                },
            ]

            article1 = {
                "doi": "10.1111/demo.10001",
                "title_en": "A Test of Policy",
                "abstract_en": "We study the policy effect.",
            }
            article2 = {
                "doi": "10.1111/demo.10002",
                "title_en": "Another Empirical Study",
                "abstract_en": "We estimate the empirical effect.",
            }
            cache = {
                "10.1111/demo.10001": {
                    "title_cn": "政策检验",
                    "abstract_cn": "我们研究了政策对经济行为的影响，并利用详细的微观数据估计了政策效应的大小与方向。",
                    "source_hash": _source_hash(article1),
                    "translation": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "prompt_version": "academic-door-abstract-zh-v2",
                        "translated_at": "2026-08-26T00:00:00+00:00",
                    },
                },
                "10.1111/demo.10002": {
                    "title_cn": "另一项实证研究",
                    "abstract_cn": "本文基于新的数据集，采用严格的实证方法估计了关键参数，并讨论了稳健性与政策含义。",
                    "source_hash": _source_hash(article2),
                    "translation": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "prompt_version": "academic-door-abstract-zh-v2",
                        "translated_at": "2026-08-26T00:00:00+00:00",
                    },
                },
            }
            self.write(cache_root, "demo.json", json.dumps(cache))

            def fake_semantic(session, dois, *, timeout):
                return {
                    "10.1111/demo.10002": {
                        "authors": ["Carol Lee"],
                        "abstract": "We estimate the empirical effect.",
                        "title": "Another Empirical Study",
                    }
                }

            with (
                patch(
                    "scripts.build_archives_from_roster_evidence._crossref_items",
                    return_value=crossref_items,
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence._semantic_scholar_metadata_batch",
                    side_effect=fake_semantic,
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence.JOURNALS_PATH",
                    journals_path,
                ),
            ):
                import requests

                session = requests.Session()
                result = process_evidence(
                    evidence_root / "demo" / "demo-1-1.json",
                    journals["journals"]["DEMO"],
                    session=session,
                    api_root=api_root,
                    state_root=state_root,
                    staging_root=staging_root,
                    translation_cache_root=cache_root,
                    max_translations=120,
                    start_year=2022,
                    timeout=10,
                )

            self.assertEqual("ready", result["result"], result)
            archive = json.loads(
                (
                    api_root / "journals" / "demo" / "issues" / "demo-1-1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("ready", archive["publication_state"])
            self.assertEqual(2, archive["quality"]["translation_complete"])

    def test_official_evidence_details_are_used_before_fallback_metadata(self) -> None:
        evidence = {
            "schema_version": "1.0",
            "capture_mode": "official-roster-evidence",
            "method": "official-page-read",
            "captured_at": "2026-08-27T00:00:00+00:00",
            "finalized": True,
            "journal_id": "ere",
            "issue_id": "ere-84-1",
            "official_url": "https://link.springer.com/journal/10640/volumes-and-issues/84-1",
            "excluded_item_count": 0,
            "items": [
                {
                    "sequence": 1,
                    "doi": "10.1007/s10640-022-00706-w",
                    "title_en": "First Research Paper",
                    "authors": ["Author One"],
                    "abstract_en": "Official Springer abstract.",
                    "source_url": "https://link.springer.com/article/10.1007/s10640-022-00706-w",
                }
            ],
        }
        issue = build_candidate_from_evidence(
            evidence,
            {"name": "Environmental and Resource Economics"},
            {"10.1007/s10640-022-00706-w": {}},
            publication_date="January 2023",
        )
        article = issue["articles"][0]
        self.assertEqual(["Author One"], article["authors"])
        self.assertEqual("Official Springer abstract.", article["abstract_en"])
        self.assertEqual(
            "https://link.springer.com/article/10.1007/s10640-022-00706-w",
            article["source_url"],
        )

    def test_staged_result_preserves_translation_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_root = root / "evidence"
            api_root = root / "api"
            state_root = root / "state"
            staging_root = root / "staging"
            cache_root = root / "cache"
            for folder in (evidence_root, api_root, state_root, staging_root, cache_root):
                folder.mkdir(parents=True, exist_ok=True)

            evidence = {
                "schema_version": "1.0",
                "capture_mode": "official-roster-evidence",
                "method": "official-page-read",
                "captured_at": "2026-08-27T00:00:00+00:00",
                "finalized": True,
                "journal_id": "demo",
                "issue_id": "demo-1-1",
                "official_url": "https://onlinelibrary.wiley.com/toc/12345678/2024/1/1",
                "excluded_item_count": 0,
                "items": [
                    {
                        "sequence": 1,
                        "doi": "10.1111/demo.10001",
                        "title_en": "A Test of Policy",
                        "authors": ["Alice Smith"],
                        "abstract_en": "Abstract: We study the policy effect.",
                        "source_url": "https://onlinelibrary.wiley.com/doi/10.1111/demo.10001",
                    }
                ],
            }
            evidence_path = evidence_root / "demo" / "demo-1-1.json"
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            def fake_translate(issue, cache_path, *, max_translations):
                self.assertEqual(
                    "We study the policy effect.",
                    issue["articles"][0]["abstract_en"],
                )
                return {
                    "journal_id": "demo",
                    "translated": 0,
                    "invalid_cache_entries": 1,
                    "upgraded_cache_entries": 0,
                    "failed": [
                        {
                            "doi": "10.1111/demo.10001",
                            "title_en": "A Test of Policy",
                            "error": "provider unavailable",
                        }
                    ],
                    "provider_state": {"deepseek": "provider unavailable"},
                    "fallback_translated": 0,
                    "model": "deepseek-chat",
                    "prompt_version": "academic-door-abstract-zh-v2",
                }

            with (
                patch(
                    "scripts.build_archives_from_roster_evidence._crossref_items",
                    return_value=[],
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence._metadata_for_dois",
                    return_value={},
                ),
                patch(
                    "scripts.build_archives_from_roster_evidence.translate_missing",
                    side_effect=fake_translate,
                ),
            ):
                import requests

                result = process_evidence(
                    evidence_path,
                    {
                        "id": "demo",
                        "name": "Demo Journal",
                        "issn": "1234-5678",
                    },
                    session=requests.Session(),
                    api_root=api_root,
                    state_root=state_root,
                    staging_root=staging_root,
                    translation_cache_root=cache_root,
                    max_translations=120,
                    start_year=2022,
                    timeout=10,
                )

            self.assertEqual("translation_partial", result["result"], result)
            self.assertEqual(1, result["translation_report"]["invalid_cache_entries"])
            self.assertEqual(
                "provider unavailable",
                result["translation_report"]["failed"][0]["error"],
            )


if __name__ == "__main__":
    unittest.main()
