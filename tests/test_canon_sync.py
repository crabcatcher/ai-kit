import re
import unittest
from pathlib import Path

from ai_kit.rules import LABELS

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "canon" / "canon.md"
REGISTRY = ROOT / "canon" / "registry.md"


class CanonSyncTests(unittest.TestCase):
    def canon_titles(self) -> dict[str, str]:
        text = CANON.read_text(encoding="utf-8")
        return dict(re.findall(r"^## (K\d+)\. (.+)$", text, re.MULTILINE))

    def registry_texts(self) -> dict[str, str]:
        text = REGISTRY.read_text(encoding="utf-8")
        return {key: value.strip() for key, value in re.findall(r"^\| (K\d+) \| ([^|]+) \|", text, re.MULTILINE)}

    def test_texts_match(self) -> None:
        self.assertEqual(self.canon_titles(), self.registry_texts())

    def test_sixteen_axioms(self) -> None:
        self.assertEqual(len(self.canon_titles()), 16)

    def test_every_label_is_named_in_k4(self) -> None:
        text = CANON.read_text(encoding="utf-8")
        start = text.index("## K4.")
        end = text.index("## K5.")
        section = text[start:end]
        for label in LABELS:
            self.assertIn(label, section, label)


if __name__ == "__main__":
    unittest.main()
