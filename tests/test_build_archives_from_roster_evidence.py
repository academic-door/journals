from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_archives_from_roster_evidence import process_evidence
from scripts.translate_issue import _source_hash


class BuildArchivesFromRosterEvidenceTests(unittest.TestCase):
    def write(self, root: Path, relative: str, value: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
