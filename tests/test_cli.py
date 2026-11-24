import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ai_kit import doctor, init, interview, profile, prompt, templates

MODULES = (doctor, init, interview, profile, prompt, templates)


class CliFlagTests(unittest.TestCase):
    def test_unknown_flag_is_error(self) -> None:
        for module in MODULES:
            with self.subTest(module=module.__name__), redirect_stderr(io.StringIO()):
                self.assertEqual(module.main(["--typo"]), 2)

    def test_known_help_is_ok(self) -> None:
        for module in MODULES:
            with self.subTest(module=module.__name__), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(["--help"]), 0)


if __name__ == "__main__":
    unittest.main()
