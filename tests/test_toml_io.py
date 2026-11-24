import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_kit.toml_io import TomlError, load


class TomlIoTests(unittest.TestCase):
    def test_utf8_bom(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.toml"
            path.write_text('schema_version = "1"\n', encoding="utf-8-sig")
            self.assertEqual(load(path)["schema_version"], "1")

    def test_error_names_file(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "broken.toml"
            path.write_text("[broken\n", encoding="utf-8")
            with self.assertRaises(TomlError) as raised:
                load(path)
            self.assertIn("broken.toml", str(raised.exception))

    def test_missing_file_names_path(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing.toml"
            with self.assertRaises(TomlError) as raised:
                load(path)
            self.assertIn("missing.toml", str(raised.exception))

    def test_invalid_utf8_names_path(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "binary.toml"
            path.write_bytes(b"\xff\xfe")
            with self.assertRaises(TomlError) as raised:
                load(path)
            self.assertIn("binary.toml", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
