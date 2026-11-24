import copy
import json
import tomllib
import unittest
from pathlib import Path

from ai_kit.profile import ProfileError, parse, print_schema
from ai_kit.rules import LABELS

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "profiles" / "example.toml"


class SchemaConsistencyTests(unittest.TestCase):
    def schema(self) -> dict:
        return json.loads(print_schema())

    def example(self) -> dict:
        with EXAMPLE.open("rb") as handle:
            return tomllib.load(handle)

    def test_required_sections(self) -> None:
        required = set(self.schema()["required"])
        self.assertEqual(
            required,
            {"schema_version", "project", "docs", "stack", "layout", "tests", "process", "automation", "security"},
        )

    def test_docs_strings_are_not_empty(self) -> None:
        docs = self.schema()["properties"]["docs"]["properties"]
        self.assertEqual(docs["language"]["minLength"], 1)
        self.assertEqual(docs["dir"]["minLength"], 1)

    def test_empty_docs_language_rejected(self) -> None:
        data = copy.deepcopy(self.example())
        data["docs"]["language"] = ""
        with self.assertRaises(ProfileError):
            parse(data)

    def test_rule_labels_match(self) -> None:
        node = self.schema()["properties"]["rules"]["items"]
        self.assertEqual(node["properties"]["label"]["enum"], list(LABELS))


if __name__ == "__main__":
    unittest.main()
