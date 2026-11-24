import unittest
from pathlib import Path

from ai_kit.prompt import launcher

ROOT = Path(__file__).resolve().parents[1]


class PromptTests(unittest.TestCase):
    def test_launcher_contains_paths(self) -> None:
        text = launcher(ROOT)
        self.assertIn(str(ROOT), text)
        self.assertIn("docs", text)
        self.assertIn("agent-prompt.md", text)
        self.assertIn("Проект: текущий каталог", text)


if __name__ == "__main__":
    unittest.main()
