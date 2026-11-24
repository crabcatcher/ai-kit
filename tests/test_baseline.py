import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_kit.baseline import BaselineError, TEMPLATE, load, parse, violation_errors


def entry(**extra):
    data = {
        "rule": "A1",
        "path": "src/Legacy/Foo",
        "reason": "принято при внедрении",
        "owner": "владелец проекта",
        "trigger": "изменение затронутого участка",
    }
    data.update(extra)
    return data


def data(*entries):
    return {"schema_version": "1", "violation": list(entries)}


class BaselineTests(unittest.TestCase):
    def test_valid(self) -> None:
        rows = parse(data(entry()), {"A1"})
        self.assertEqual(rows[0].rule, "A1")

    def test_template_is_valid(self) -> None:
        self.assertEqual(parse(tomllib.loads(TEMPLATE)), [])

    def test_unknown_rule_rejected(self) -> None:
        paths = [path for path, _ in violation_errors(data(entry()), {"K1"})]
        self.assertIn("violation[0].rule", paths)

    def test_missing_trigger_rejected(self) -> None:
        paths = [path for path, _ in violation_errors(data(entry(trigger="—")))]
        self.assertIn("violation[0].trigger", paths)

    def test_absolute_path_rejected(self) -> None:
        paths = [path for path, _ in violation_errors(data(entry(path="/etc/passwd")))]
        self.assertIn("violation[0].path", paths)

    def test_duplicate_place_rejected(self) -> None:
        paths = [path for path, _ in violation_errors(data(entry(), entry()))]
        self.assertIn("violation[1]", paths)

    def test_missing_file_is_empty(self) -> None:
        with TemporaryDirectory() as temporary:
            self.assertEqual(load(Path(temporary) / "baseline.toml"), [])

    def test_bad_file_raises(self) -> None:
        with self.assertRaises(BaselineError):
            parse(data(entry(trigger="")))


if __name__ == "__main__":
    unittest.main()
