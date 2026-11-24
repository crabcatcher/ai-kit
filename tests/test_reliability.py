import io
import os
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_kit import cli
from ai_kit.args import UsageError, ensure_known, normalize
from ai_kit.init import _write_all, generate
from ai_kit.init import Request
from ai_kit.interview import InterviewError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "profiles" / "example.toml"
ADAPTER = ROOT / "tests" / "fixtures" / "adapter" / "adapter.toml"


class ArgsTests(unittest.TestCase):
    def test_normalize_short_help(self) -> None:
        self.assertEqual(normalize(["-h"]), ["--help"])

    def test_normalize_equals(self) -> None:
        self.assertEqual(normalize(["--profile=x"]), ["--profile", "x"])

    def test_single_dash_rejected(self) -> None:
        with self.assertRaises(UsageError):
            ensure_known(["-x"], {"--help"})


class CliTests(unittest.TestCase):
    def test_empty_help(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([]), 0)

    def test_unknown_command(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["nope"]), 2)


class WriteTests(unittest.TestCase):
    def test_preparation_failure_keeps_original(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "document.md"
            original.write_text("old", encoding="utf-8")
            blocker = root / "blocker"
            blocker.write_text("file", encoding="utf-8")
            with self.assertRaises(OSError):
                _write_all([(original, "new"), (blocker / "child.md", "x")])
            self.assertEqual(original.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(root.glob(".*.tmp")), [])

    @unittest.skipIf(os.name == "nt", "POSIX mode")
    def test_written_file_mode(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.md"
            _write_all([(path, "text")])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o777, 0o644)


class BaselineCliTests(unittest.TestCase):
    def test_custom_baseline_name(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            generate(
                Request(
                    target=target,
                    kit_root=ROOT,
                    profile=EXAMPLE,
                    adapter=ADAPTER,
                    write_baseline="custom.toml",
                )
            )
            self.assertTrue((target / "custom.toml").is_file())

    def test_broken_json_is_init_error(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            answers = target / "answers.json"
            answers.write_text("{", encoding="utf-8")
            with self.assertRaises(InterviewError):
                generate(
                    Request(
                        target=target,
                        kit_root=ROOT,
                        profile=EXAMPLE,
                        adapter=ADAPTER,
                        answers=answers,
                    )
                )

    def test_invalid_utf8_json_is_init_error(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            answers = target / "answers.json"
            answers.write_bytes(b"\xff\xfe")
            with self.assertRaises(InterviewError) as raised:
                generate(
                    Request(
                        target=target,
                        kit_root=ROOT,
                        profile=EXAMPLE,
                        adapter=ADAPTER,
                        answers=answers,
                    )
                )
            self.assertIn("answers.json", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
