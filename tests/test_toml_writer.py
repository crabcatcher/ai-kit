import tomllib
import unittest
from pathlib import Path

from ai_kit.toml_writer import TomlWriteError, dumps

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "profiles" / "example.toml"


class WriterTests(unittest.TestCase):
    def roundtrip(self, data: dict) -> dict:
        return tomllib.loads(dumps(data))

    def test_scalars(self) -> None:
        data = {"schema_version": "1", "flag": True, "count": 3, "ratio": 1.5}
        self.assertEqual(self.roundtrip(data), data)

    def test_string_escapes(self) -> None:
        data = {"text": 'кавычка " и слэш \\ и перевод\nстроки'}
        self.assertEqual(self.roundtrip(data), data)

    def test_nested_table(self) -> None:
        data = {"tests": {"framework": "runner", "commands": {"unit": "run-unit"}}}
        self.assertEqual(self.roundtrip(data), data)

    def test_array_of_tables(self) -> None:
        data = {"gates": {"disabled": [{"id": "layers", "trigger": "второй разработчик"}]}}
        self.assertEqual(self.roundtrip(data), data)

    def test_empty_list(self) -> None:
        self.assertEqual(self.roundtrip({"stack": {"frameworks": []}}), {"stack": {"frameworks": []}})

    def test_empty_table(self) -> None:
        self.assertEqual(self.roundtrip({"tests": {}}), {"tests": {}})

    def test_quoted_key(self) -> None:
        data = {"tests": {"commands": {"run unit": "x"}}}
        self.assertEqual(self.roundtrip(data), data)

    def test_mixed_list_rejected(self) -> None:
        with self.assertRaises(TomlWriteError):
            dumps({"mix": [1, {"a": 1}]})

    def test_unsupported_value_rejected(self) -> None:
        with self.assertRaises(TomlWriteError):
            dumps({"value": object()})

    def test_example_profile_roundtrip(self) -> None:
        with EXAMPLE.open("rb") as handle:
            data = tomllib.load(handle)
        self.assertEqual(self.roundtrip(data), data)


if __name__ == "__main__":
    unittest.main()
