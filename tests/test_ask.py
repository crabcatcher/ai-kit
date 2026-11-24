import unittest

from ai_kit.ask import AskAborted, collect, confirm
from ai_kit.interview import ABSENT, Question
from ai_kit.templates import COMMAND_ROLES


def question(*slots: str) -> Question:
    return Question(id="q", text="Вопрос?", slots=slots, why="так надо", source="universal")


def answers(questions: list[Question], inputs: list[str], values: dict | None = None):
    queue = iter(inputs)

    def ask(prompt: str) -> str:
        return next(queue)

    return collect(questions, values or {}, ask=ask, tell=lambda line: None)


class CollectTests(unittest.TestCase):
    def test_string(self) -> None:
        result = answers([question("project.purpose")], ["продукт"])
        self.assertEqual(result[0].value, "продукт")

    def test_empty_is_unknown(self) -> None:
        result = answers([question("project.purpose")], [""])
        self.assertEqual(result[0].status, "unknown")

    def test_known_slot_is_not_asked(self) -> None:
        result = answers([question("project.purpose")], [], {"project": {"purpose": "уже есть"}})
        self.assertEqual(result, [])

    def test_boolean(self) -> None:
        self.assertIs(answers([question("process.tiers")], ["да"])[0].value, True)
        self.assertIs(answers([question("process.tiers")], ["n"])[0].value, False)

    def test_boolean_retries(self) -> None:
        result = answers([question("process.tiers")], ["может", "нет"])
        self.assertIs(result[0].value, False)

    def test_list(self) -> None:
        result = answers([question("layout.source_roots")], ["app, routes, tests"])
        self.assertEqual(result[0].value, ["app", "routes", "tests"])

    def test_list_from_json(self) -> None:
        result = answers([question("layout.source_roots")], ['["app", "routes"]'])
        self.assertEqual(result[0].value, ["app", "routes"])

    def test_command_roles(self) -> None:
        inputs = ["run-test", "", "run-e2e", "", "", ""]
        result = answers([question("tests.commands")], inputs)
        self.assertEqual(result[0].value, {"test": "run-test", "e2e": "run-e2e"})

    def test_empty_commands_is_unknown(self) -> None:
        result = answers([question("tests.commands")], [""] * len(COMMAND_ROLES))
        self.assertEqual(result[0].status, "unknown")

    def test_evidence_name(self) -> None:
        result = answers([question("stack.runtime")], ["PHP"])
        self.assertEqual(result[0].name, "PHP")
        self.assertIs(result[0].value, ABSENT)

    def test_eof_aborts(self) -> None:
        def broken(prompt: str) -> str:
            raise EOFError

        with self.assertRaises(AskAborted):
            collect([question("project.purpose")], {}, ask=broken, tell=lambda line: None)


class ConfirmTests(unittest.TestCase):
    def test_yes(self) -> None:
        for value in ("y", "yes", "д", "да"):
            self.assertTrue(confirm(ask=lambda prompt, value=value: value))

    def test_no(self) -> None:
        for value in ("", "n", "нет", "что-то"):
            self.assertFalse(confirm(ask=lambda prompt, value=value: value))


if __name__ == "__main__":
    unittest.main()
