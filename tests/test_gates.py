import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_kit.gates import (
    Gate,
    load_gates,
    parse_version,
    probe_binary,
    run_gates,
    satisfies,
)

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tests" / "fixtures" / "adapter" / "adapter.toml"
PYTHON = sys.executable


def adapter() -> dict:
    with ADAPTER.open("rb") as handle:
        return tomllib.load(handle)


def gate(**overrides) -> dict:
    data = {
        "id": "check",
        "invariant": "Инвариант",
        "requires": {"binary": PYTHON, "version": ">=3"},
        "command": ["python", "-c", "import sys; sys.exit(0)"],
        "on_unavailable": "gap",
    }
    data.update(overrides)
    return data


class VersionTests(unittest.TestCase):
    def test_parse_version(self) -> None:
        self.assertEqual(parse_version("Python 3.12.10"), "3.12.10")
        self.assertIsNone(parse_version("unknown"))

    def test_satisfies_range(self) -> None:
        self.assertTrue(satisfies("1.2", ">=1.0 <2.0"))
        self.assertFalse(satisfies("2.0", ">=1.0 <2.0"))
        self.assertTrue(satisfies("3.12.10", ">=3"))
        self.assertFalse(satisfies("3.12.10", ">3.12.10"))

    def test_bad_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            satisfies("1.0", "~1.0")


class GateParsingTests(unittest.TestCase):
    def paths(self, entries: list[dict]) -> list[str]:
        _, diagnostics = load_gates({"gates": entries})
        return [item.path for item in diagnostics]

    def test_fixture_gate_is_valid(self) -> None:
        gates, diagnostics = load_gates(adapter())
        self.assertEqual(diagnostics, [])
        self.assertEqual(gates[0].id, "layers")
        self.assertEqual(gates[0].argv[0], "layer-check")

    def test_config_placeholder_rejected(self) -> None:
        self.assertIn("gates[0].command", self.paths([gate(command="check --config {config}")]))

    def test_string_command_is_split(self) -> None:
        gates, diagnostics = load_gates({"gates": [gate(command="check --all")]})
        self.assertEqual(diagnostics, [])
        self.assertEqual(gates[0].argv, ("check", "--all"))

    def test_missing_on_unavailable_rejected(self) -> None:
        entry = gate()
        del entry["on_unavailable"]
        self.assertIn("gates[0].on_unavailable", self.paths([entry]))

    def test_unknown_field_rejected(self) -> None:
        self.assertIn("gates[0].note", self.paths([gate(note="x")]))

    def test_duplicate_id_rejected(self) -> None:
        self.assertIn("gates[1].id", self.paths([gate(), gate()]))

    def test_bad_version_range_rejected(self) -> None:
        self.assertIn("gates[0].requires.version", self.paths([gate(requires={"binary": "x", "version": "~1"})]))

    def test_empty_command_rejected(self) -> None:
        self.assertIn("gates[0].command", self.paths([gate(command=[])]))


class GateRunTests(unittest.TestCase):
    def run_one(self, code: int) -> list:
        item = Gate("check", "Инвариант", PYTHON, ">=3", (PYTHON, "-c", f"import sys; sys.exit({code})"))
        return run_gates([item], ROOT)

    def test_successful_gate_is_silent(self) -> None:
        self.assertEqual(self.run_one(0), [])

    def test_violation_is_error(self) -> None:
        result = self.run_one(1)
        self.assertEqual(result[0].severity, "error")
        self.assertIn("нарушение", result[0].message)

    def test_config_error_is_error(self) -> None:
        self.assertIn("конфигурации", self.run_one(2)[0].message)

    def test_stub_is_gap(self) -> None:
        result = self.run_one(3)
        self.assertEqual(result[0].severity, "gap")
        self.assertIn("не реализован", result[0].message)

    def test_unexpected_code_is_error(self) -> None:
        self.assertIn("9", self.run_one(9)[0].message)

    def test_missing_binary_is_gap(self) -> None:
        item = Gate("check", "Инвариант", "no-such-binary-xyz", None, ("no-such-binary-xyz",))
        result = run_gates([item], ROOT)
        self.assertEqual(result[0].severity, "gap")
        self.assertIn("недоступен", result[0].message)

    def test_version_mismatch_is_gap(self) -> None:
        version, error = probe_binary(PYTHON, ROOT)
        self.assertIsNone(error)
        self.assertIsNotNone(version)
        item = Gate("check", "Инвариант", PYTHON, ">=99", (PYTHON, "-c", "pass"))
        result = run_gates([item], ROOT)
        self.assertEqual(result[0].severity, "gap")
        self.assertIn("не подходит", result[0].message)

    def test_baseline_is_substituted(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "baseline.toml").write_text('schema_version = "1"\n', encoding="utf-8")
            code = (
                "import pathlib, sys; "
                "raise SystemExit(0 if pathlib.Path(sys.argv[1]).is_file() else 1)"
            )
            item = Gate("check", "Инвариант", PYTHON, None, (PYTHON, "-c", code, "{baseline}"))
            self.assertEqual(run_gates([item], root), [])

    def test_missing_baseline_is_gap(self) -> None:
        with TemporaryDirectory() as temporary:
            item = Gate("check", "Инвариант", PYTHON, None, (PYTHON, "-c", "pass", "{baseline}"))
            result = run_gates([item], Path(temporary))
            self.assertEqual(result[0].severity, "gap")
            self.assertIn("baseline.toml", result[0].message)


if __name__ == "__main__":
    unittest.main()
