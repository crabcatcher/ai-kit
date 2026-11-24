import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_kit import adapter

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "profiles" / "example.toml"
FIXTURE = ROOT / "tests" / "fixtures" / "adapter" / "adapter.toml"


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, text: str) -> Path:
        path = self.project / "adapter.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def validate(self, text: str):
        return adapter.validate(self.write(text), EXAMPLE, self.project)

    def codes(self, report) -> list[str]:
        return [item.code for item in report.diagnostics]

    def test_fixture_is_valid(self) -> None:
        report = adapter.validate(FIXTURE, EXAMPLE, self.project)
        self.assertEqual(report.errors, 0, report.to_table())

    def test_unknown_top_level_section(self) -> None:
        report = self.validate("[[probe]]\nid = \"x\"\n")
        self.assertIn("adapter", self.codes(report))

    def test_bad_probe(self) -> None:
        report = self.validate(
            "[[probes]]\nid = \"p\"\nglobs = [\"manifest.json\"]\n"
            "field = \"stack.runtime\"\nname = \"runtime\"\nextract = \"xml:x\"\n"
            "provenance = \"probe\"\n"
        )
        self.assertTrue(
            any(
                item.severity == "error"
                and "probes[0].extract" in item.path
                for item in report.diagnostics
            ),
            report.to_table(),
        )

    def test_duplicate_scalar_probe(self) -> None:
        report = self.validate(
            "[[probes]]\nid = \"a\"\nglobs = [\"manifest.json\"]\n"
            "field = \"tests.framework\"\nextract = \"json:x\"\nprovenance = \"probe\"\n"
            "[[probes]]\nid = \"b\"\nglobs = [\"manifest.json\"]\n"
            "field = \"tests.framework\"\nextract = \"json:y\"\nprovenance = \"probe\"\n"
        )
        self.assertTrue(any(item.severity == "error" and "скалярное" in item.message for item in report.diagnostics))

    def test_duplicate_fact_is_error(self) -> None:
        report = self.validate(
            "[[probes]]\nid = \"a\"\nglobs = [\"manifest.json\"]\n"
            "field = \"stack.runtime\"\nname = \"runtime\"\nextract = \"json:x\"\nprovenance = \"probe\"\n"
            "[[probes]]\nid = \"b\"\nglobs = [\"manifest.json\"]\n"
            "field = \"stack.runtime\"\nname = \"runtime\"\nextract = \"json:y\"\nprovenance = \"probe\"\n"
        )
        self.assertTrue(
            any(
                item.severity == "error" and "две пробы" in item.message
                for item in report.diagnostics
            )
        )

    def test_minimal_profile_with_docs_is_valid(self) -> None:
        profile = self.project / "base.toml"
        profile.write_text('schema_version = "1"\n\n[docs]\ndir = ".project"\n', encoding="utf-8")
        report = adapter.validate(FIXTURE, profile, self.project)
        self.assertEqual(report.errors, 0, report.to_table())

    def test_profile_without_docs_is_diagnostic(self) -> None:
        profile = self.project / "base.toml"
        profile.write_text('schema_version = "1"\n', encoding="utf-8")
        report = adapter.validate(FIXTURE, profile, self.project)
        self.assertEqual(report.errors, 0, report.to_table())
        self.assertTrue(
            any(
                item.code == "profile"
                and item.severity == "warning"
                and "docs.dir" in item.path
                for item in report.diagnostics
            ),
            report.to_table(),
        )

    def test_fragment_feature_unreadable_is_diagnostic(self) -> None:
        feature = self.project / ".project" / "features" / "health.md"
        feature.parent.mkdir(parents=True)
        feature.write_bytes(b"\xff\xfe\x00")
        report = self.validate(
            "[[docs_fragments]]\ntarget = \"features/health.md\"\n"
            "marker = \"adapter:contracts\"\ncontent = \"| x | y |\"\n"
        )
        self.assertGreaterEqual(report.errors, 1, report.to_table())
        self.assertTrue(
            any(item.code == "fragment" and "не читается" in item.message for item in report.diagnostics),
            report.to_table(),
        )

    def test_fragment_table_header_conflict(self) -> None:
        report = self.validate(
            "[[docs_fragments]]\ntarget = \"architecture.md\"\n"
            "marker = \"adapter:decisions\"\n"
            "content = \"| Решение | Причина | Что отвергли |\\n|---|---|---|\\n| x | y | z |\"\n"
        )
        self.assertTrue(
            any(
                item.code == "fragment" and item.severity == "error" and "шапка" in item.message
                for item in report.diagnostics
            )
        )

    def test_gate_without_rule(self) -> None:
        report = self.validate(
            "[[gates]]\nid = \"g\"\ninvariant = \"нет правила\"\n"
            "requires = { binary = \"python\" }\ncommand = [\"python\", \"-c\", \"0\"]\n"
            "on_unavailable = \"gap\"\n"
        )
        self.assertTrue(any(item.code == "reference" for item in report.diagnostics))

    def test_input_feature_missing_is_warning(self) -> None:
        report = self.validate(
            "[[inputs]]\nid = \"health\"\nkind = \"http\"\nlocator = \"GET /health\"\n"
            "feature = \"features/missing.md\"\n"
        )
        self.assertTrue(
            any(item.code == "inputs" and item.severity == "warning" for item in report.diagnostics)
        )

    def test_probes_flag_reports_misses(self) -> None:
        text = (
            "[[probes]]\nid = \"p\"\nglobs = [\"missing.json\"]\n"
            "field = \"stack.runtime\"\nname = \"runtime\"\nextract = \"json:version\"\n"
            "provenance = \"probe\"\n"
        )
        without = self.validate(text)
        self.assertFalse(any(item.code == "probe" for item in without.diagnostics))
        with_probes = adapter.validate(self.write(text), EXAMPLE, self.project, run_probes=True)
        self.assertTrue(
            any(item.code == "probe" and item.severity == "warning" for item in with_probes.diagnostics)
        )

    def test_fragment_content_heading_rejected(self) -> None:
        report = self.validate(
            "[[docs_fragments]]\ntarget = \"architecture.md\"\n"
            "marker = \"adapter:decisions\"\ncontent = \"# Заголовок\"\n"
        )
        self.assertTrue(any(item.code == "fragment" and item.severity == "error" for item in report.diagnostics))

    def test_fragment_target_missing_rejected(self) -> None:
        report = self.validate(
            "[[docs_fragments]]\ntarget = \"nope.md\"\n"
            "marker = \"adapter:decisions\"\ncontent = \"x\"\n"
        )
        self.assertTrue(any(item.code == "fragment" and item.severity == "error" for item in report.diagnostics))

    def test_cli_json(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = adapter.main(["validate", "--adapter", str(FIXTURE), "--profile", str(EXAMPLE), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["summary"]["errors"], 0)


if __name__ == "__main__":
    unittest.main()
