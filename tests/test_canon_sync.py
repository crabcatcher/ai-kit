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

    def test_current_state_is_axiom(self) -> None:
        text = CANON.read_text(encoding="utf-8")
        section = text[text.index("## K1.") : text.index("## K2.")]
        agents = (ROOT / "templates" / "AGENTS.md.tpl").read_text(encoding="utf-8")
        agents = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", agents)
        self.assertIn("текущее состояние", self.canon_titles()["K1"])
        for phrase in (
            "конструкции перехода",
            "«было/стало»",
            "«раньше/теперь»",
            "«уже …»",
            "«пока (не) …»",
            "«ещё (не) …»",
            "«раньше/позже» допустимы только о порядке шагов в сценарии",
            "Прошлое остаётся в истории репозитория и описании изменения",
            "хронология уместна только в журнале сбоев, где запись — событие (K13)",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)
                self.assertIn(phrase, agents)

    def test_every_label_is_named_in_k4(self) -> None:
        text = CANON.read_text(encoding="utf-8")
        start = text.index("## K4.")
        end = text.index("## K5.")
        section = text[start:end]
        for label in LABELS:
            self.assertIn(label, section, label)


if __name__ == "__main__":
    unittest.main()
