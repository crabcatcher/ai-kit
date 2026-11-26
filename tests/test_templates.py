import copy
import io
import os
import posixpath
import re
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ai_kit.templates import (
    Document,
    RenderContext,
    TemplateError,
    document_path,
    load_documents,
    marker_names,
    render,
    render_document,
    validate_documents,
    validate_template,
    main as templates_main,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "profiles" / "example.toml"
DOCUMENTS = load_documents(ROOT / "documents.toml", ROOT)
DOCUMENT_IDS = {document.id for document in DOCUMENTS}
COMMANDS = {
    "unit": "run-unit",
    "e2e": "run-e2e",
    "lint": "run-lint",
    "build": "run-build",
    "migrate": "run-migrate",
}


def profile() -> dict:
    with PROFILE_PATH.open("rb") as handle:
        return tomllib.load(handle)


def context(item: Document, *, values: dict | None = None, commands=None, fragments=None) -> RenderContext:
    return RenderContext(
        document=item,
        documents=DOCUMENTS,
        root=ROOT,
        values=profile() if values is None else values,
        commands=COMMANDS if commands is None else commands,
        fragments={} if fragments is None else fragments,
    )


def find_document(document_id: str) -> Document:
    return next(item for item in DOCUMENTS if item.id == document_id)


class ManifestTests(unittest.TestCase):
    def test_manifest_is_valid(self) -> None:
        self.assertEqual(validate_documents(ROOT, DOCUMENTS), [])
        self.assertEqual(len(DOCUMENTS), 17)

    def test_check_works_from_other_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            previous = os.getcwd()
            os.chdir(temporary)
            try:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(templates_main(["--check"]), 0)
            finally:
                os.chdir(previous)

    def test_render_works_from_other_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            previous = os.getcwd()
            os.chdir(temporary)
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(
                        templates_main(
                            ["--render", "architecture", "--profile", str(ROOT / "profiles" / "example.toml")]
                        ),
                        0,
                    )
                self.assertIn("# Архитектура", output.getvalue())
            finally:
                os.chdir(previous)

    def test_paths_are_unique(self) -> None:
        paths = [document_path(item, profile()) for item in DOCUMENTS]
        self.assertEqual(len(paths), len(set(paths)))

    def test_duplicate_id_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "t.md").write_text("# X\n", encoding="utf-8")
            manifest = root / "documents.toml"
            manifest.write_text(
                '[[document]]\nid = "a"\ntemplate = "t.md"\npath = "a.md"\ntitle = "A"\n\n'
                '[[document]]\nid = "a"\ntemplate = "t.md"\npath = "b.md"\ntitle = "B"\n',
                encoding="utf-8",
            )
            with self.assertRaises(TemplateError) as raised:
                load_documents(manifest, root)
            self.assertIn("идентификатор повторяется", str(raised.exception))

    def test_unknown_placeholder_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "t.md").write_text("# X\n", encoding="utf-8")
            manifest = root / "documents.toml"
            manifest.write_text(
                '[[document]]\nid = "a"\ntemplate = "t.md"\npath = "{project.name}/a.md"\ntitle = "A"\n',
                encoding="utf-8",
            )
            with self.assertRaises(TemplateError) as raised:
                load_documents(manifest, root)
            self.assertIn("неизвестная подстановка", str(raised.exception))

    def test_missing_template_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "documents.toml"
            manifest.write_text(
                '[[document]]\nid = "a"\ntemplate = "missing.md"\npath = "a.md"\ntitle = "A"\n',
                encoding="utf-8",
            )
            with self.assertRaises(TemplateError) as raised:
                load_documents(manifest, root)
            self.assertIn("файла нет", str(raised.exception))


class TemplateValidationTests(unittest.TestCase):
    def errors(self, source: str) -> list[str]:
        return validate_template(source, document_ids=DOCUMENT_IDS)

    def test_unknown_slot_rejected(self) -> None:
        self.assertTrue(any("project.missing" in error for error in self.errors("# X\n\n{{ project.missing }}\n")))

    def test_unknown_link_rejected(self) -> None:
        self.assertTrue(any("link:missing" in error for error in self.errors("# X\n\n{{link:missing}}\n")))

    def test_unknown_command_role_rejected(self) -> None:
        self.assertTrue(any("cmd:format" in error for error in self.errors("# X\n\n{{cmd:format}}\n")))

    def test_unclosed_block_rejected(self) -> None:
        self.assertTrue(any("не закрыт" in error for error in self.errors("# X\n\n{{#each project.boundaries}}\n")))

    def test_extra_close_rejected(self) -> None:
        self.assertTrue(any("без открывающего" in error for error in self.errors("# X\n\n{{/each}}\n")))

    def test_mismatched_close_rejected(self) -> None:
        source = "# X\n\n{{#each project.boundaries}}{{/if}}\n"
        self.assertTrue(any("не совпадает" in error for error in self.errors(source)))

    def test_this_outside_each_rejected(self) -> None:
        self.assertTrue(any("this" in error for error in self.errors("# X\n\n{{ this }}\n")))

    def test_unclosed_marker_rejected(self) -> None:
        source = "# X\n\n<!-- adapter:systems:begin -->\n"
        self.assertTrue(any("не закрыт" in error for error in self.errors(source)))

    def test_duplicate_marker_rejected(self) -> None:
        source = (
            "# X\n\n<!-- adapter:systems:begin -->\n<!-- adapter:systems:end -->\n"
            "<!-- adapter:systems:begin -->\n<!-- adapter:systems:end -->\n"
        )
        self.assertTrue(any("больше одного раза" in error for error in self.errors(source)))

    def test_two_first_level_headings_rejected(self) -> None:
        self.assertTrue(any("первого уровня" in error for error in self.errors("# A\n\n# B\n")))

    def test_schema_covers_all_templates(self) -> None:
        self.assertEqual(validate_documents(ROOT, DOCUMENTS), [])

    def test_template_notes_are_not_rendered(self) -> None:
        source = "<!-- заметка {{ project.missing }} -->\n# X\n"
        self.assertEqual(self.errors(source), [])


class RenderTests(unittest.TestCase):
    def rendered(self, document_id: str, **kwargs) -> str:
        return render_document(context(find_document(document_id), **kwargs))

    def test_every_document_renders(self) -> None:
        for item in DOCUMENTS:
            with self.subTest(document=item.id):
                output = render_document(context(item))
                self.assertNotIn("{{", output)
                self.assertNotIn("None", output)
                self.assertTrue(output.endswith("\n"))
                self.assertEqual(output.count("\n# "), 0)

    def test_links_point_to_documents(self) -> None:
        known = {document_path(item, profile()) for item in DOCUMENTS}
        for item in DOCUMENTS:
            output = render_document(context(item))
            for target in re.findall(r"\]\(([^)]+)\)", output):
                with self.subTest(document=item.id, link=target):
                    current = document_path(item, profile())
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(current), target))
                    self.assertIn(resolved, known)

    def test_root_document_links_into_docs_dir(self) -> None:
        self.assertIn("(.project/README.md)", self.rendered("readme"))

    def test_same_directory_link_is_explicit(self) -> None:
        self.assertIn("(./architecture.md)", self.rendered("docs-index"))

    def test_missing_command_is_visible(self) -> None:
        output = self.rendered("agents", commands={})
        self.assertIn("`гейтов нет`", output)

    def test_not_applicable_is_visible(self) -> None:
        values = profile()
        values["not_applicable"] = [
            {"field": "automation.clients", "reason": "клиентов агента нет"}
        ]
        output = self.rendered("architecture", values=values)
        self.assertIn("Осознанно неприменимо:", output)
        self.assertIn("automation.clients", output)
        self.assertIn("клиентов агента нет", output)

    def test_command_is_substituted(self) -> None:
        output = self.rendered("agents")
        self.assertIn("`run-unit`", output)

    def test_adapter_fragment_is_inserted(self) -> None:
        row = "| пример | проверка | проект |"
        output = self.rendered("architecture", fragments={"adapter:systems": row})
        self.assertIn(row, output)
        self.assertIn("<!-- adapter:systems:begin -->", output)

    def test_empty_fragment_keeps_section_with_marker(self) -> None:
        output = self.rendered("architecture")
        self.assertIn("## Внешние системы", output)
        self.assertIn("## Решения", output)
        self.assertIn("<!-- adapter:systems:begin -->", output)

    def test_empty_generated_table_removes_section(self) -> None:
        output = self.rendered("registry")
        self.assertNotIn("## Канон", output)
        self.assertNotIn("## Стек и архитектура", output)
        self.assertIn("## Метки", output)

    def test_generated_fragment_fills_table(self) -> None:
        row = "| K1 | пример правила | гейт | проверка | ревьюер | владелец | — |"
        output = self.rendered("registry", fragments={"generated:canon": row})
        self.assertIn("## Канон", output)
        self.assertIn(row, output)

    def test_empty_list_keeps_adapter_section(self) -> None:
        values = copy.deepcopy(profile())
        values["layout"]["layers"] = []
        output = self.rendered("architecture", values=values)
        self.assertIn("## Слои", output)
        self.assertIn("<!-- adapter:layers:begin -->", output)

    def test_disabled_tiers_remove_section(self) -> None:
        values = copy.deepcopy(profile())
        del values["process"]["tiers"]
        output = self.rendered("pipeline", values=values)
        self.assertNotIn("## Уровни", output)

    def test_tech_debt_lists_disabled_gate(self) -> None:
        output = self.rendered("tech-debt")
        self.assertIn("| layers |", output)
        self.assertIn("второй разработчик", output)

    def test_optional_secrets_policy_is_rendered(self) -> None:
        text = "секреты только в окружении, не в репозитории"
        self.assertIn(text, self.rendered("security"))

    def test_final_answer_is_fact_and_short(self) -> None:
        output = self.rendered("pipeline")
        self.assertIn("факт по артефактам", output)
        self.assertIn("Ответ — без воды", output)

    def test_docs_index_requires_current_state(self) -> None:
        output = self.rendered("docs-index")
        self.assertIn("Только текущее состояние", output)
        self.assertIn("хронология запрещена", output)

    def test_review_exempts_incident_log(self) -> None:
        output = self.rendered("review")
        self.assertIn("нет хронологии", output)
        self.assertIn("журнал сбоев", output)

    def test_development_requires_fresh_git_state(self) -> None:
        self.assertIn("git status --short", self.rendered("development"))

    def test_fragment_with_heading_rejected(self) -> None:
        with self.assertRaises(TemplateError):
            self.rendered("architecture", fragments={"adapter:systems": "# Свой раздел"})

    def test_fragment_for_unknown_marker_rejected(self) -> None:
        with self.assertRaises(TemplateError) as raised:
            self.rendered("architecture", fragments={"adapter:missing": "текст"})
        self.assertIn("маркера нет", str(raised.exception))

    def test_fragment_with_substitution_rejected(self) -> None:
        with self.assertRaises(TemplateError):
            self.rendered("architecture", fragments={"adapter:systems": "{{ project.name }}"})

    def test_render_rejects_invalid_template(self) -> None:
        source = "# X\n\n{{ project.missing }}\n"
        with self.assertRaises(TemplateError):
            render(source, context(find_document("readme")))

    def test_marker_names_ignore_template_notes(self) -> None:
        source = (
            "<!-- заметка: <!-- adapter:hidden:begin --> -->\n"
            "<!-- adapter:layers:begin -->\nтекст\n<!-- adapter:layers:end -->\n"
        )
        self.assertEqual(marker_names(source, "adapter"), ["layers"])

    def test_marker_names_reject_unknown_kind(self) -> None:
        with self.assertRaises(TemplateError):
            marker_names("", "other")


if __name__ == "__main__":
    unittest.main()
