import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]


class IssueSchemaTests(unittest.TestCase):
    def test_development_fixture_matches_schema(self):
        schema = json.loads(
            (ROOT / "schemas" / "issue.schema.json").read_text(encoding="utf-8")
        )
        issue = json.loads(
            (
                ROOT
                / "public"
                / "api"
                / "v1"
                / "journals"
                / "aer"
                / "issues"
                / "current.json"
            ).read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = list(validator.iter_errors(issue))
        self.assertEqual([], [error.message for error in errors])

    def test_sequence_is_contiguous_and_doi_is_unique(self):
        issue = json.loads(
            (
                ROOT
                / "public"
                / "api"
                / "v1"
                / "journals"
                / "aer"
                / "issues"
                / "current.json"
            ).read_text(encoding="utf-8")
        )
        sequences = [article["sequence"] for article in issue["articles"]]
        self.assertEqual(list(range(1, len(sequences) + 1)), sequences)
        dois = [article["doi"] for article in issue["articles"] if article["doi"]]
        self.assertEqual(len(dois), len(set(dois)))


if __name__ == "__main__":
    unittest.main()
