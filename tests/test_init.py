import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_kit.init import InitError, Request, generate
from ai_kit.profile import parse
from ai_kit.templates import TemplateError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "profiles" / "example.toml"
ADAPTER = ROOT / "tests" / "fixtures" / "adapter" / "adapter.toml"

ANSWERS = """
[[answer]]
slot = "project.name"
value = "Новый проект"

[[answer]]
slot = "project.purpose"
value = "Проверить генератор на пустом месте"

[[answer]]
slot = "project.boundaries"
value = ["без домена"]

[[answer]]
slot = "layout.source_roots"
value = ["src"]

[[answer]]
slot = "process.review_roles"
value = ["reviewer"]

[[answer]]
slot = "process.tiers"
value = true

[[answer]]
slot = "automation.pre_commit"
value = false

[[answer]]
slot = "security.access_levels"
value = ["owner"]

[[answer]]
slot = "stack.frameworks"
name = "framework"
version = "1.0"
"""


class InitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.target = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self, **overrides) -> Request:
        data = {"target": self.target, "kit_root": ROOT, "profile": EXAMPLE, "adapter": ADAPTER}
        data.update(overrides)
        return Request(**data)

    def read(self, relative: str) -> str:
        return (self.target / relative).read_text(encoding="utf-8")

    def test_profile_and_documents_are_written(self) -> None:
        report = generate(self.request())
        self.assertEqual(len(report.documents), 16)
        self.assertTrue((self.target / "profile.toml").is_file())
        parse(tomllib.loads(self.read("profile.toml")))
        self.assertIn("# example", self.read("README.md"))

    def test_registry_contains_all_spaces(self) -> None:
        generate(self.request())
        registry = self.read(".project/registry.md")
        self.assertIn("| K1 |", registry)
        self.assertIn("| A1 |", registry)
        self.assertIn("| P1 |", registry)

    def test_agents_lists_rules(self) -> None:
        generate(self.request())
        agents = self.read("AGENTS.md")
        self.assertIn("- K1 —", agents)
        self.assertIn("- A1 —", agents)
        self.assertIn("- P1 —", agents)

    def test_adapter_fragment_is_inserted(self) -> None:
        generate(self.request())
        self.assertIn("| пример | проверка формы | сложность |", self.read(".project/architecture.md"))

    def test_manual_adapter_fragment_is_preserved(self) -> None:
        path = self.target / ".project" / "architecture.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "# Архитектура\n\n<!-- adapter:layers:begin -->\nручной текст\n<!-- adapter:layers:end -->\n",
            encoding="utf-8",
        )
        generate(self.request())
        self.assertIn("ручной текст", self.read(".project/architecture.md"))

    def test_second_run_keeps_files_identical(self) -> None:
        generate(self.request())
        first = {
            path.relative_to(self.target): path.read_text(encoding="utf-8")
            for path in self.target.rglob("*")
            if path.is_file()
        }
        generate(self.request())
        second = {
            path.relative_to(self.target): path.read_text(encoding="utf-8")
            for path in self.target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(first, second)

    def test_dry_run_writes_nothing(self) -> None:
        report = generate(self.request(dry_run=True))
        self.assertTrue(report.dry_run)
        self.assertFalse((self.target / "profile.toml").exists())
        self.assertFalse((self.target / ".project").exists())

    def test_scratch_project_from_answers(self) -> None:
        answers = self.target / "answers.toml"
        answers.write_text(ANSWERS, encoding="utf-8")
        generate(self.request(profile=None, answers=answers))
        profile = parse(tomllib.loads(self.read("profile.toml")))
        self.assertEqual(profile.project.name, "Новый проект")
        self.assertEqual(profile.docs.dir, ".project")
        self.assertEqual(profile.docs.language, "ru")
        self.assertIn("# Правила работы с Новый проект", self.read("AGENTS.md"))

    def test_scratch_profile_records_unknown_version(self) -> None:
        answers = self.target / "answers.toml"
        answers.write_text(ANSWERS, encoding="utf-8")
        generate(self.request(profile=None, answers=answers))
        profile = parse(tomllib.loads(self.read("profile.toml")))
        fields = [item.field for item in profile.unknowns]
        self.assertIn("stack.frameworks.version", fields)

    def test_known_field_is_not_unknown(self) -> None:
        generate(self.request())
        profile = parse(tomllib.loads(self.read("profile.toml")))
        fields = [item.field for item in profile.unknowns]
        self.assertNotIn("project.purpose", fields)
        self.assertNotIn("stack.runtime", fields)

    def test_empty_input_is_rejected(self) -> None:
        with self.assertRaises(InitError):
            generate(self.request(profile=None))

    def test_profile_outside_target_is_rejected(self) -> None:
        with self.assertRaises(InitError):
            generate(self.request(write_profile="../escape.toml"))

    def test_fragment_with_unknown_target_is_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "nope.md"\nmarker = "adapter:layers"\ncontent = "x"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_invalid_adapter_rule_is_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[rules]]\nid = "A1"\ntext = "x"\nlabel = "foo"\n'
            'executor = "check"\ncatcher = "гейт"\nowner = "владелец"\ntrigger = "—"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_fragment_with_heading_is_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "architecture.md"\n'
            'marker = "adapter:layers"\ncontent = "# Свой раздел"\n',
            encoding="utf-8",
        )
        with self.assertRaises(TemplateError):
            generate(self.request(adapter=adapter))


if __name__ == "__main__":
    unittest.main()
