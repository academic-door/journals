import unittest

from scripts.import_official_roster_evidence import validate_evidence
from scripts.parse_wiley_citation_export import build_evidence, parse_ris


class WileyCitationExportTests(unittest.TestCase):
    RIS = """TY  - JOUR
DO  - 10.1111/jofi.13190
TI  - Pricing Currency Risks
AU  - Chernov, Mikhail
ER  -
TY  - JOUR
DO  - 10.1111/jofi.13213
TI  - A Model of Systemic Bank Runs
AU  - Liu, Xuewen
ER  -
"""

    def test_builds_conservative_roster_evidence_without_inventing_abstracts(self) -> None:
        evidence = build_evidence(
            self.RIS,
            issue_id="jf-78-2",
            official_url="https://onlinelibrary.wiley.com/toc/15406261/2023/78/2",
            captured_at="2026-08-30T00:00:00Z",
        )
        validate_evidence(evidence)
        self.assertEqual(2, len(evidence["items"]))
        self.assertNotIn("abstract_en", evidence["items"][0])
        self.assertNotIn("authors", evidence["items"][0])

    def test_ignores_records_without_doi_or_title(self) -> None:
        records = parse_ris("TY  - JOUR\nTI  - Missing DOI\nER  -\n")
        self.assertEqual([], records)


if __name__ == "__main__":
    unittest.main()
