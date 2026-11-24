import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_kit import cli, doctor, init, interview, profile, prompt, templates

MODULES = (doctor, init, interview, profile, prompt, templates)


class CliFlagTests(unittest.TestCase):
    def test_cli_configures_console(self) -> None:
        with patch("ai_kit.cli.configure_console") as configure, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--help"]), 0)
        configure.assert_called_once_with()

    def test_unknown_flag_is_error(self) -> None:
        for module in MODULES:
            with self.subTest(module=module.__name__), redirect_stderr(io.StringIO()):
                self.assertEqual(module.main(["--typo"]), 2)

    def test_known_help_is_ok(self) -> None:
        for module in MODULES:
            with self.subTest(module=module.__name__), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(["--help"]), 0)

    def test_short_help_is_ok(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(init.main(["-h"]), 0)

    def test_profile_flags_are_exclusive(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(profile.main(["--check", "--print-schema"]), 2)

    def test_profile_check_requires_value(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(profile.main(["--check"]), 2)

    def test_profile_invalid_utf8_returns_one(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "binary.toml"
            path.write_bytes(b"\xff\xfe")
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(profile.main(["--check", str(path)]), 1)
            self.assertIn("binary.toml", error.getvalue())


if __name__ == "__main__":
    unittest.main()
