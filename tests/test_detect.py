import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_kit.detect import load, matches


class DetectTests(unittest.TestCase):
    def test_fixture_shape(self) -> None:
        patterns, errors = load({"detect": [{"glob": "manifest.json"}]})
        self.assertEqual(errors, [])
        self.assertEqual(patterns, ["manifest.json"])

    def test_plain_string_allowed(self) -> None:
        patterns, errors = load({"detect": ["manifest.json"]})
        self.assertEqual(errors, [])
        self.assertEqual(patterns, ["manifest.json"])

    def test_absolute_glob_rejected(self) -> None:
        _, errors = load({"detect": [{"glob": "/etc/passwd"}]})
        self.assertIn("detect[0]", [path for path, _ in errors])

    def test_parent_glob_rejected(self) -> None:
        _, errors = load({"detect": [{"glob": "../manifest.json"}]})
        self.assertIn("detect[0]", [path for path, _ in errors])

    def test_match_in_project(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            self.assertTrue(matches(root, ["manifest.json"]))
            self.assertFalse(matches(root, ["other.json"]))


if __name__ == "__main__":
    unittest.main()
