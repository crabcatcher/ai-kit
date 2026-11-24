import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_kit.doctor import DoctorRequest, main, run
from ai_kit.init import Request, generate

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "profiles" / "example.toml"
FIXTURE = ROOT / "tests" / "fixtures" / "adapter" / "adapter.toml"
PYTHON = json.dumps(sys.executable)


def gate_adapter(*, code: int = 0, rules: bool = True, binary: str = PYTHON) -> str:
    parts = []
    if rules:
        parts.append(
            '[[rules]]\nid = "A1"\ntext = "Инвариант проверки"\nlabel = "гейт"\n'
            'executor = "case-check"\ncatcher = "гейт"\nowner = "владелец проекта"\ntrigger = "—"\n'
        )
    parts.append(
        "[[gates]]\n"
        'id = "case-check"\n'
        'invariant = "Инвариант проверки"\n'
        f"requires = {{ binary = {binary} }}\n"
        f"command = [{PYTHON}, \"-c\", \"import sys; sys.exit({code})\"]\n"
        'on_unavailable = "gap"\n'
    )
    return "\n".join(parts)


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.target = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make(self, adapter: Path | None = None) -> None:
        generate(Request(target=self.target, kit_root=ROOT, profile=EXAMPLE, adapter=adapter))

    def doctor(self, adapter: Path | None = None, *, gates: bool = False):
        return run(
            DoctorRequest(
                root=self.target,
                profile=self.target / "profile.toml",
                adapter=adapter,
                kit_root=ROOT,
                gates=gates,
            )
        )

    def write_adapter(self, text: str) -> Path:
        path = self.target / "adapter.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def append(self, relative: str, text: str) -> None:
        path = self.target / relative
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")

    def test_clean_project_has_no_errors(self) -> None:
        self.make()
        report = self.doctor()
        self.assertTrue(report.ok, report.to_table())
        self.assertEqual([item for item in report.diagnostics if item.severity == "error"], [])

    def test_empty_adapter_fragment_warns(self) -> None:
        self.make()
        report = self.doctor()
        codes = [item.code for item in report.diagnostics]
        self.assertIn("adapter", codes)

    def test_fixture_gates_are_not_run_without_flag(self) -> None:
        self.make(FIXTURE)
        report = self.doctor(FIXTURE)
        self.assertTrue(report.ok)
        gate = [item for item in report.diagnostics if item.code == "gate" and item.path == "gates"]
        self.assertEqual(gate[0].severity, "warning")

    def test_broken_link_reported(self) -> None:
        self.make()
        self.append(".project/development.md", "\n[битая](missing.md)\n")
        report = self.doctor()
        self.assertFalse(report.ok)
        self.assertIn("link", [item.code for item in report.diagnostics])

    def test_leftover_placeholder_reported(self) -> None:
        self.make()
        self.append(".project/patterns.md", "\n{{ project.name }}\n")
        report = self.doctor()
        self.assertIn("placeholder", [item.code for item in report.diagnostics])

    def test_empty_section_is_warning(self) -> None:
        self.make()
        self.append(".project/development.md", "\n## Пусто\n")
        report = self.doctor()
        self.assertTrue(report.ok)
        self.assertIn("section", [item.code for item in report.diagnostics])

    def test_missing_document_reported(self) -> None:
        self.make()
        (self.target / ".project" / "templates" / "plan.md").unlink()
        report = self.doctor()
        self.assertIn("document", [item.code for item in report.diagnostics])

    def test_profile_error_reported(self) -> None:
        self.make()
        path = self.target / "profile.toml"
        text = path.read_text(encoding="utf-8").replace('label = "дисциплина"', 'label = "нет гейта"', 1)
        path.write_text(text, encoding="utf-8")
        report = self.doctor()
        self.assertIn("profile", [item.code for item in report.diagnostics])

    def test_bad_baseline_reported(self) -> None:
        self.make()
        (self.target / "baseline.toml").write_text(
            'schema_version = "1"\n\n'
            '[[violation]]\nrule = "A404"\npath = "app/Legacy.php"\n'
            'reason = "старое"\nowner = "владелец"\ntrigger = "—"\n',
            encoding="utf-8",
        )
        report = self.doctor()
        self.assertIn("baseline", [item.code for item in report.diagnostics])

    def test_custom_baseline_path_reported(self) -> None:
        self.make()
        (self.target / "custom-baseline.toml").write_text(
            'schema_version = "1"\n\n'
            '[[violation]]\nrule = "A404"\npath = "app/Legacy.php"\n'
            'reason = "старое"\nowner = "владелец"\ntrigger = "—"\n',
            encoding="utf-8",
        )
        report = run(
            DoctorRequest(
                root=self.target,
                profile=self.target / "profile.toml",
                adapter=None,
                kit_root=ROOT,
                baseline="custom-baseline.toml",
            )
        )
        self.assertTrue(any("custom-baseline.toml" in item.path for item in report.diagnostics))

    def test_missing_precommit_reported(self) -> None:
        self.make()
        (self.target / "tools" / "pre-commit.py").unlink()
        report = self.doctor()
        self.assertIn("precommit", [item.code for item in report.diagnostics])

    def test_orphan_feature_reported(self) -> None:
        self.make()
        path = self.target / ".project" / "features" / "login.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Вход\n", encoding="utf-8")
        report = self.doctor()
        self.assertIn("features", [item.code for item in report.diagnostics])

    def test_disabled_gate_is_gap(self) -> None:
        self.make()
        report = self.doctor()
        gaps = [item for item in report.diagnostics if item.severity == "gap" and item.path == "layers"]
        self.assertEqual(len(gaps), 1)

    def test_declared_adapter_gap(self) -> None:
        adapter = self.write_adapter(
            '[[gaps]]\nid = "manual-sign"\nreason = "нет автоматической проверки"\n'
            'owner = "владелец проекта"\ntrigger = "появление проверки подписи"\n'
        )
        self.make(adapter)
        report = self.doctor(adapter)
        gaps = [item for item in report.diagnostics if item.severity == "gap" and item.path == "manual-sign"]
        self.assertEqual(len(gaps), 1)

    def test_stale_registry_reported(self) -> None:
        self.make()
        path = self.target / ".project" / "registry.md"
        path.write_text(path.read_text(encoding="utf-8").replace("| K1 |", "| Q1 |"), encoding="utf-8")
        report = self.doctor()
        self.assertIn("registry", [item.code for item in report.diagnostics])

    def test_disabled_gate_missing_from_debt_reported(self) -> None:
        self.make()
        path = self.target / ".project" / "tech-debt.md"
        text = path.read_text(encoding="utf-8").replace(
            "| layers | инструмент проверки ещё не выбран | владелец проекта | второй разработчик |\n",
            "",
        )
        path.write_text(text, encoding="utf-8")
        report = self.doctor()
        self.assertIn("registry", [item.code for item in report.diagnostics])

    def test_gate_without_registry_row_reported(self) -> None:
        adapter = self.write_adapter(gate_adapter(rules=False))
        self.make(adapter)
        report = self.doctor(adapter)
        self.assertIn("registry", [item.code for item in report.diagnostics])

    def test_gate_violation_is_error(self) -> None:
        adapter = self.write_adapter(gate_adapter(code=1))
        self.make(adapter)
        report = self.doctor(adapter, gates=True)
        self.assertFalse(report.ok)
        self.assertIn("нарушение", report.to_table())

    def test_stub_is_gap_and_registry_error(self) -> None:
        adapter = self.write_adapter(gate_adapter(code=3))
        self.make(adapter)
        report = self.doctor(adapter, gates=True)
        severities = [item.severity for item in report.diagnostics]
        self.assertIn("gap", severities)
        self.assertIn("error", severities)

    def test_unavailable_gate_is_gap(self) -> None:
        adapter = self.write_adapter(gate_adapter(binary='"no-such-binary-xyz"'))
        self.make(adapter)
        report = self.doctor(adapter, gates=True)
        gaps = [item for item in report.diagnostics if item.severity == "gap" and item.path == "case-check"]
        self.assertEqual(len(gaps), 1)
        self.assertIn("недоступен", gaps[0].message)

    def test_json_report_is_machine_readable(self) -> None:
        self.make()
        report = self.doctor()
        data = json.loads(report.to_json())
        self.assertEqual(data["summary"]["errors"], 0)

    def test_cli_requires_profile(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main([]), 2)

    def test_cli_returns_zero_for_clean_project(self) -> None:
        self.make()
        with patch("pathlib.Path.cwd", return_value=self.target), redirect_stdout(io.StringIO()):
            code = main(["--profile", str(self.target / "profile.toml")])
        self.assertEqual(code, 0)

    def test_cli_returns_one_for_broken_document(self) -> None:
        self.make()
        self.append(".project/development.md", "\n[битая](missing.md)\n")
        with patch("pathlib.Path.cwd", return_value=self.target), redirect_stdout(io.StringIO()):
            code = main(["--profile", str(self.target / "profile.toml")])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
