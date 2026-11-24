import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / "tools" / "forbidden_terms.toml").read_text(encoding="utf-8"))
SUFFIXES = {".md", ".tpl", ".toml"}


class CoreCleanlinessTests(unittest.TestCase):
    def test_no_forbidden_terms(self) -> None:
        files = [
            path
            for folder in CONFIG["scope"]
            for path in (ROOT / folder).rglob("*")
            if path.is_file() and path.suffix in SUFFIXES
        ]
        self.assertTrue(files, "область проверки пуста")
        hits = []
        for path in files:
            text = path.read_text(encoding="utf-8").lower()
            for term in CONFIG["terms"]:
                if term in text:
                    hits.append(f"{path.relative_to(ROOT)}: {term}")
        self.assertEqual(hits, [])

    def test_scope_covers_core_texts(self) -> None:
        for folder in ("canon", "templates", "interview"):
            self.assertIn(folder, CONFIG["scope"])


if __name__ == "__main__":
    unittest.main()
