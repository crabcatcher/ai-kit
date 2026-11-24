import unittest

from ai_kit.precommit import files, hook, script, select_commands, split_command


class PrecommitTests(unittest.TestCase):
    def data(self, commands: dict) -> dict:
        return {"tests": {"commands": commands}}

    def test_unit_and_lint_preferred(self) -> None:
        selected = select_commands(self.data({"test": "t", "unit": "u", "lint": "l"}))
        self.assertEqual(selected, [("unit", "u"), ("lint", "l")])

    def test_test_is_fallback(self) -> None:
        self.assertEqual(select_commands(self.data({"test": "t"})), [("test", "t")])

    def test_no_commands(self) -> None:
        self.assertEqual(files(self.data({})), {})

    def test_files_and_scripts(self) -> None:
        generated = files(self.data({"unit": "php vendor/bin/phpunit"}))
        self.assertIn("tools/pre-commit.py", generated)
        self.assertIn("cmd", generated["tools/pre-commit.py"])
        self.assertIn("python3", generated["tools/hooks/pre-commit"])

    def test_split_command(self) -> None:
        self.assertEqual(split_command("php vendor/bin/phpunit"), ["php", "vendor/bin/phpunit"])

    def test_script_contains_arguments(self) -> None:
        text = script([("unit", ["php", "vendor/bin/phpunit"])])
        self.assertIn("'php'", text)
        self.assertIn("vendor/bin/phpunit", text)

    def test_hook_checks_python(self) -> None:
        text = hook()
        self.assertIn("py -3", text)
        self.assertIn("hooks.interpreter", text)
        self.assertIn("AI_KIT_PYTHON", text)

    def test_script_sets_utf8(self) -> None:
        self.assertIn("reconfigure", script([("unit", ["run"])]))


if __name__ == "__main__":
    unittest.main()
