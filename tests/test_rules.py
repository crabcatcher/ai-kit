import unittest
from pathlib import Path

from ai_kit.rules import build_rules, load_canon, rule_errors

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "canon" / "registry.md"


def entry(**extra):
    data = {
        "id": "P1",
        "text": "Ветка называется по задаче",
        "label": "дисциплина",
        "executor": "—",
        "catcher": "ревьюер",
        "owner": "владелец проекта",
        "trigger": "—",
    }
    data.update(extra)
    return data


class CanonTests(unittest.TestCase):
    def test_every_axiom_is_parsed(self) -> None:
        rows = load_canon(CANON)
        self.assertEqual(len(rows), 16)
        self.assertEqual([row.id for row in rows], [f"K{index}" for index in range(1, 17)])
        self.assertEqual(rows[0].label, "гейт частичный")

    def test_gate_has_executor(self) -> None:
        rows = {row.id: row for row in load_canon(CANON)}
        self.assertNotEqual(rows["K4"].executor, "—")


class RuleErrorTests(unittest.TestCase):
    def paths(self, entries, space: str = "P") -> list[str]:
        return [path for path, _ in rule_errors(entries, space, "rules")]

    def test_valid_rule(self) -> None:
        self.assertEqual(rule_errors([entry()], "P", "rules"), [])

    def test_wrong_space_rejected(self) -> None:
        self.assertIn("rules[0].id", self.paths([entry(id="A1")]))

    def test_unknown_label_rejected(self) -> None:
        self.assertIn("rules[0].label", self.paths([entry(label="пожелание")]))

    def test_gate_without_executor_rejected(self) -> None:
        self.assertIn("rules[0].executor", self.paths([entry(label="гейт")]))

    def test_no_gate_without_reason_rejected(self) -> None:
        paths = self.paths([entry(label="нет гейта", trigger="второй разработчик")])
        self.assertIn("rules[0].reason", paths)

    def test_no_gate_without_trigger_rejected(self) -> None:
        paths = self.paths([entry(label="нет гейта", reason="инструмента нет")])
        self.assertIn("rules[0].trigger", paths)

    def test_discipline_without_catcher_rejected(self) -> None:
        self.assertIn("rules[0].catcher", self.paths([entry(catcher="—")]))

    def test_unknown_field_rejected(self) -> None:
        self.assertIn("rules[0].note", self.paths([entry(note="x")]))

    def test_duplicate_number_rejected(self) -> None:
        self.assertIn("rules[1].id", self.paths([entry(), entry()]))

    def test_build_keeps_reason(self) -> None:
        rule = build_rules([entry(label="нет гейта", reason="нет инструмента")])[0]
        self.assertEqual(rule.reason, "нет инструмента")


if __name__ == "__main__":
    unittest.main()
