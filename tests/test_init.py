import io
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_kit.init import InitError, Request, generate
from ai_kit.interview import InterviewError, apply_commands
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

    def write_feature(self, name: str, text: str) -> Path:
        path = self.target / ".project" / "features" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""# Функция

## Назначение

{text}

## Логика

Порядок действий.

## Контракты

| ID входа | Выход | Доступ | Кто вызывает |
|---|---|---|---|
<!-- adapter:contracts:begin -->
<!-- adapter:contracts:end -->

## Где в коде

| Часть | Путь |
|---|---|
| — | — |

## Нюансы

Нет.

## Что не разобрано

Нет.
""",
            encoding="utf-8",
        )
        return path

    def test_profile_and_documents_are_written(self) -> None:
        report = generate(self.request())
        self.assertEqual(len(report.documents), 17)
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
        self.assertFalse((self.target / "baseline.toml").exists())
        self.assertFalse((self.target / "tools").exists())

    def test_baseline_is_created(self) -> None:
        generate(self.request())
        self.assertTrue((self.target / "baseline.toml").is_file())

    def test_precommit_artifacts_are_created(self) -> None:
        generate(self.request())
        script = self.read("tools/pre-commit.py")
        self.assertIn("run-unit", script)
        self.assertTrue((self.target / "tools" / "hooks" / "pre-commit").is_file())

    def test_features_are_indexed_from_files(self) -> None:
        generate(self.request())
        path = self.target / ".project" / "features" / "login.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Вход\n", encoding="utf-8")
        generate(self.request())
        self.assertIn("| Вход | `features/login.md` |", self.read(".project/FEATURES.md"))

    def test_feature_with_bom_is_indexed(self) -> None:
        generate(self.request())
        path = self.target / ".project" / "features" / "bom.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xef\xbb\xbf# \xd0\x92\xd1\x85\xd0\xbe\xd0\xb4\n")
        generate(self.request())
        self.assertIn("| Вход | `features/bom.md` |", self.read(".project/FEATURES.md"))

    def test_unreadable_document_is_init_error(self) -> None:
        generate(self.request())
        (self.target / ".project" / "architecture.md").write_bytes(b"\xff\xfe\x00")
        with self.assertRaises(InitError):
            generate(self.request())

    def test_fragment_list_of_wrong_type_is_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text("docs_fragments = 5\n", encoding="utf-8")
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_adapter_gaps_are_rendered_in_debt(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[gaps]]\nid = "manual"\nreason = "нет автоматической проверки"\n'
            'owner = "владелец"\ntrigger = "появление проверки"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        self.assertIn("нет автоматической проверки", self.read(".project/tech-debt.md"))

    def test_adapter_inputs_seed_profile(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[inputs]]\nid = "health"\nkind = "http"\nlocator = "GET /health"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        data = tomllib.loads(self.read("profile.toml"))
        self.assertEqual(
            data["inputs"],
            [{"id": "health", "kind": "http", "locator": "GET /health"}],
        )

    def test_adapter_inputs_conflict_with_profile(self) -> None:
        profile = self.target / "profile.toml"
        profile.write_text(
            EXAMPLE.read_text(encoding="utf-8")
            + '\n[[inputs]]\nid = "health"\nkind = "http"\nlocator = "GET /health"\n',
            encoding="utf-8",
        )
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[inputs]]\nid = "other"\nkind = "http"\nlocator = "GET /other"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(profile=profile, adapter=adapter))

    def test_invalid_adapter_inputs_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[inputs]]\nid = "Bad"\nkind = "http"\nlocator = "GET /health"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_refresh_keeps_profile_inputs(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[inputs]]\nid = "health"\nkind = "http"\nlocator = "GET /health"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        before = self.read("profile.toml")
        adapter.write_text(
            '[[inputs]]\nid = "other"\nkind = "http"\nlocator = "GET /other"\n',
            encoding="utf-8",
        )
        generate(self.request(refresh=True, profile=self.target / "profile.toml", adapter=adapter))
        self.assertEqual(self.read("profile.toml"), before)

    def test_refresh_updates_generated_fragments_only(self) -> None:
        generate(self.request())
        profile_before = self.read("profile.toml")
        feature = self.target / ".project" / "features" / "login.md"
        feature.parent.mkdir(parents=True)
        feature.write_text("# Вход\n", encoding="utf-8")
        (self.target / "baseline.toml").write_text(
            'schema_version = "1"\n\n'
            '[[violation]]\nrule = "P1"\npath = "src/Legacy"\nreason = "старое"\n'
            'owner = "владелец проекта"\ntrigger = "изменение участка"\n',
            encoding="utf-8",
        )
        generator = self.read("tools/pre-commit.py")
        report = generate(
            self.request(refresh=True, profile=self.target / "profile.toml", adapter=ADAPTER)
        )
        self.assertIn("features/login.md", self.read(".project/FEATURES.md"))
        self.assertIn("src/Legacy", self.read(".project/tech-debt.md"))
        self.assertEqual(self.read("profile.toml"), profile_before)
        self.assertEqual(self.read("tools/pre-commit.py"), generator)
        self.assertIn(".project/FEATURES.md", report.overwritten)
        self.assertIn(".project/tech-debt.md", report.overwritten)
        self.assertNotIn("README.md", report.overwritten)

    def test_refresh_requires_profile(self) -> None:
        with self.assertRaises(InitError):
            generate(self.request(refresh=True, profile=None))

    def test_bad_baseline_is_init_error(self) -> None:
        generate(self.request())
        (self.target / "baseline.toml").write_text(
            'schema_version = "1"\n[[violation]]\nrule = "P1"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(refresh=True, profile=self.target / "profile.toml", adapter=ADAPTER))

    def test_overwritten_files_are_reported(self) -> None:
        (self.target / "README.md").write_text("# Старый\n", encoding="utf-8")
        report = generate(self.request())
        self.assertIn("README.md", report.overwritten)

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

    def test_fragment_from_content_file(self) -> None:
        adapter = self.target / "adapter.toml"
        fragments = self.target / "fragments"
        fragments.mkdir()
        (fragments / "layers.md").write_text("| из файла | правило | триггер |\n", encoding="utf-8")
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "architecture.md"\n'
            'marker = "adapter:layers"\ncontent_file = "fragments/layers.md"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        self.assertIn("| из файла | правило | триггер |", self.read(".project/architecture.md"))

    def test_fragment_from_content_file_with_bom(self) -> None:
        adapter = self.target / "adapter.toml"
        fragments = self.target / "fragments"
        fragments.mkdir()
        (fragments / "layers.md").write_text(
            "\ufeff| из файла | правило | триггер |\n", encoding="utf-8"
        )
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "architecture.md"\n'
            'marker = "adapter:layers"\ncontent_file = "fragments/layers.md"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        architecture = self.read(".project/architecture.md")
        self.assertNotIn("\ufeff", architecture)
        self.assertIn("| из файла | правило | триггер |", architecture)

    def test_fragment_content_and_file_are_exclusive(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "architecture.md"\n'
            'marker = "adapter:layers"\ncontent = "x"\ncontent_file = "fragments/layers.md"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_fragment_content_file_outside_project_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "architecture.md"\n'
            'marker = "adapter:layers"\ncontent_file = "../escape.md"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_fragment_content_file_absolute_rejected(self) -> None:
        fragment = self.target / "escape.md"
        fragment.write_text("| x | y | z |\n", encoding="utf-8")
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "architecture.md"\n'
            f'marker = "adapter:layers"\ncontent_file = "{fragment.as_posix()}"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_scratch_profile_records_not_applicable(self) -> None:
        answers = self.target / "answers.toml"
        answers.write_text(
            ANSWERS
            + '\n[[answer]]\nslot = "tests.framework"\nstatus = "not_applicable"\n'
            'reason = "автотестов нет"\n',
            encoding="utf-8",
        )
        generate(self.request(profile=None, answers=answers))
        parse(tomllib.loads(self.read("profile.toml")))
        data = tomllib.loads(self.read("profile.toml"))
        self.assertEqual(
            data["not_applicable"],
            [{"field": "tests.framework", "reason": "автотестов нет"}],
        )

    def test_not_applicable_removes_existing_unknown(self) -> None:
        profile = self.target / "profile.toml"
        profile.write_text(
            EXAMPLE.read_text(encoding="utf-8")
            + '\n[[unknowns]]\nfield = "automation.clients"\nquestion = "Какие клиенты агента?"\n',
            encoding="utf-8",
        )
        answers = self.target / "answers.toml"
        answers.write_text(
            '[[answer]]\nslot = "automation.clients"\nstatus = "not_applicable"\n'
            'reason = "клиентов агента нет"\n',
            encoding="utf-8",
        )
        generate(self.request(profile=profile, answers=answers))
        data = tomllib.loads(self.read("profile.toml"))
        fields = [entry["field"] for entry in data.get("unknowns", [])]
        self.assertNotIn("automation.clients", fields)

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

    def test_fragment_with_duplicate_table_header_is_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "architecture.md"\n'
            'marker = "adapter:decisions"\n'
            'content = "| Решение | Причина | Что отвергли |\\n|---|---|---|\\n| x | y | z |"\n',
            encoding="utf-8",
        )
        with self.assertRaises(TemplateError):
            generate(self.request(adapter=adapter))

    def test_fragment_marker_missing_in_manifest_is_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "architecture.md"\n'
            'marker = "adapter:nope"\ncontent = "x"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_feature_fragment_is_inserted(self) -> None:
        feature = self.write_feature("login.md", "ручной текст")
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/login.md"\n'
            'marker = "adapter:contracts"\ncontent = "| `health` | 200 | public | browser |"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        text = feature.read_text(encoding="utf-8")
        self.assertIn("| `health` | 200 | public | browser |", text)
        self.assertIn("ручной текст", text)
        report = generate(self.request(adapter=adapter))
        self.assertEqual(len(report.documents), 17)
        self.assertEqual(report.features, (".project/features/login.md",))

    def test_feature_fragment_missing_file_is_error(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/missing.md"\n'
            'marker = "adapter:contracts"\ncontent = "x"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_feature_fragment_nested_path_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/sub/login.md"\n'
            'marker = "adapter:contracts"\ncontent = "x"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_feature_fragment_preserves_bom_and_crlf(self) -> None:
        feature = self.write_feature("login.md", "ручной текст")
        feature.write_bytes(
            b"\xef\xbb\xbf"
            + feature.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8")
        )
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/login.md"\n'
            'marker = "adapter:contracts"\ncontent = "| `health` | 200 | public | browser |"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        raw = feature.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", raw)
        self.assertIn("ручной текст".encode("utf-8"), raw)
        self.assertIn("| `health` | 200 | public | browser |".encode("utf-8"), raw)

    def test_feature_fragment_refresh_updates(self) -> None:
        feature = self.write_feature("login.md", "ручной текст")
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/login.md"\n'
            'marker = "adapter:contracts"\ncontent = "| `first` | 200 | public | browser |"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/login.md"\n'
            'marker = "adapter:contracts"\ncontent = "| `second` | 200 | public | browser |"\n',
            encoding="utf-8",
        )
        generate(self.request(refresh=True, profile=self.target / "profile.toml", adapter=adapter))
        text = feature.read_text(encoding="utf-8")
        self.assertIn("`second`", text)
        self.assertNotIn("`first`", text)

    def test_feature_fragment_marker_missing_is_error(self) -> None:
        feature = self.write_feature("login.md", "ручной текст")
        feature.write_text(
            feature.read_text(encoding="utf-8").replace(
                "<!-- adapter:contracts:begin -->\n<!-- adapter:contracts:end -->",
                "<!-- adapter:other:begin -->\n<!-- adapter:other:end -->",
            ),
            encoding="utf-8",
        )
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/login.md"\n'
            'marker = "adapter:contracts"\ncontent = "x"\n',
            encoding="utf-8",
        )
        with self.assertRaises(TemplateError):
            generate(self.request(adapter=adapter))

    def test_feature_duplicate_marker_is_error(self) -> None:
        feature = self.write_feature("login.md", "ручной текст")
        feature.write_text(
            feature.read_text(encoding="utf-8").replace(
                "<!-- adapter:contracts:end -->",
                "<!-- adapter:contracts:end -->\n<!-- adapter:contracts:begin -->\n<!-- adapter:contracts:end -->",
            ),
            encoding="utf-8",
        )
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/login.md"\n'
            'marker = "adapter:contracts"\ncontent = "x"\n',
            encoding="utf-8",
        )
        with self.assertRaises(TemplateError):
            generate(self.request(adapter=adapter))

    def test_fragment_target_template_rejected(self) -> None:
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "templates/feature.md"\n'
            'marker = "adapter:contracts"\ncontent = "x"\n',
            encoding="utf-8",
        )
        with self.assertRaises(InitError):
            generate(self.request(adapter=adapter))

    def test_feature_fragment_unchanged_not_reported(self) -> None:
        self.write_feature("login.md", "ручной текст")
        adapter = self.target / "adapter.toml"
        adapter.write_text(
            '[[docs_fragments]]\ntarget = "features/login.md"\n'
            'marker = "adapter:contracts"\ncontent = "| `health` | 200 | public | browser |"\n',
            encoding="utf-8",
        )
        generate(self.request(adapter=adapter))
        report = generate(self.request(adapter=adapter))
        self.assertNotIn(".project/features/login.md", report.overwritten)

    def test_adapter_commands_fill_missing_roles(self) -> None:
        data = apply_commands({"tests": {}}, {"commands": [{"role": "test", "command": "run-tests"}]})
        self.assertEqual(data["tests"]["commands"], {"test": "run-tests"})

    def test_adapter_commands_reject_unknown_role(self) -> None:
        with self.assertRaises(InterviewError):
            apply_commands({}, {"commands": [{"role": "smoke", "command": "run"}]})

    def test_adapter_commands_conflict_with_not_applicable(self) -> None:
        data = {
            "tests": {},
            "not_applicable": [{"field": "tests.commands", "reason": "команд нет"}],
        }
        with self.assertRaises(InterviewError):
            apply_commands(data, {"commands": [{"role": "test", "command": "run-tests"}]})

    def test_ask_cancel_writes_nothing(self) -> None:
        with patch("builtins.input", side_effect=["", "", "", "n"]), redirect_stdout(io.StringIO()):
            report = generate(self.request(ask=True))
        self.assertTrue(report.cancelled)
        self.assertFalse((self.target / "profile.toml").exists())

    def test_ask_yes_writes_after_interview(self) -> None:
        with patch("builtins.input", side_effect=["", "", ""]), redirect_stdout(io.StringIO()):
            report = generate(self.request(ask=True, yes=True))
        self.assertFalse(report.cancelled)
        self.assertTrue((self.target / "profile.toml").is_file())
        parse(tomllib.loads(self.read("profile.toml")))


if __name__ == "__main__":
    unittest.main()
