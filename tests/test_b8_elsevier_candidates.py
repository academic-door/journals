"""Publisher-supplied Elsevier issue staging cannot silently become official."""
import copy
import unittest

from scripts.b8_elsevier_candidates import SOURCE_SPECS, candidate_for
from collectors.article_types import evidence_roster_article_type


class B8ElsevierCandidatesTests(unittest.TestCase):
    def source(self, key):
        spec = SOURCE_SPECS[key]
        rows = []
        for i in range(spec["count"]):
            doi = f"10.1016/j.synthetic.{key.lower()}.2026.{i+1}"
            rows.append({
                "doi": doi,
                "title_en": f"Research study {i+1}: an empirical economics analysis",
                "authors": ["Research Author", "Secondary Author"],
                "abstract_en": "This synthetic economic research paper studies market "
                               "outcomes and establishes a clear empirical association "
                               "using a transparent research design and data.",
                "abstract_source_url": (
                    f"https://ideas.repec.org/a/eee/{spec['series']}/example-{i}.html"
                ),
                "publisher_article_url": (
                    f"https://www.sciencedirect.com/science/article/pii/S000000{i:07d}"
                ),
                "crossref_url": f"https://api.crossref.org/works/{doi}",
            })
        return rows

    def test_four_independent_publisher_sources_never_claim_official(self):
        self.assertEqual({"IJIO", "JFI", "EJPE", "JIMF"}, set(SOURCE_SPECS))
        self.assertEqual(36, sum(x["count"] for x in SOURCE_SPECS.values()))
        for key, spec in SOURCE_SPECS.items():
            with self.subTest(key=key):
                serial = f"https://ideas.repec.org/s/eee/{spec['series']}.html"
                issue = candidate_for(
                    key, serial, self.source(key),
                    captured_at="2026-10-28T09:00:00+00:00",
                )
                self.assertEqual("publisher_verified", issue["source_status"])
                self.assertEqual(spec["count"], len(issue["articles"]))
                self.assertFalse(issue["quality"]["first_party_official_issue_toc_verified"])
                self.assertEqual("repec-publisher-supplied", issue["quality"]["roster_authority"])
                self.assertEqual(serial, issue["source_url"])

    def test_misreporting_is_not_an_editorial(self):
        self.assertEqual(
            "research-article",
            evidence_roster_article_type("Debt misreporting and bank lending"),
        )
        self.assertEqual("editorial", evidence_roster_article_type("Annual report"))
        self.assertEqual("editorial", evidence_roster_article_type("Editor report"))

    def test_duplicate_and_missing_articles_fail(self):
        spec = SOURCE_SPECS["IJIO"]
        rows = self.source("IJIO")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            candidate_for(
                "IJIO", "https://ideas.repec.org/s/eee/indorg.html", rows[:-1],
                captured_at="2026-10-28T09:00:00+00:00",
            )
        copyrows = copy.deepcopy(rows)
        copyrows[1]["doi"] = copyrows[0]["doi"]
        with self.assertRaisesRegex(ValueError, "duplicate DOI"):
            candidate_for(
                "IJIO", "https://ideas.repec.org/s/eee/indorg.html", copyrows,
                captured_at="2026-10-28T09:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()
