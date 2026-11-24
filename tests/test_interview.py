import io
import json
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_kit.interview import (
    ADAPTER_LIMIT,
    MERGED_LIMIT,
    UNIVERSAL_LIMIT,
    Answer,
    Bank,
    InterviewError,
    Prepared,
    Probe,
    Question,
    apply_answers,
    apply_probe_hits,
    describe_slot,
    finish,
    is_known,
    load_adapter_questions,
    load_answers,
    load_probes,
    load_questions,
    main as interview_main,
    merge_questions,
    parse_answers,
    parse_probes,
    parse_questions,
    plan_payload,
    prepare,
    report,
    run_probes,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "interview" / "questions.toml"
ADAPTER = ROOT / "tests" / "fixtures" / "adapter" / "adapter.toml"
EXAMPLE = ROOT / "profiles" / "example.toml"


def entry(*, id: str = "one", slots=("project.purpose",), **extra):
    data = {"id": id, "question": "Вопрос?", "slots": list(slots), "why": "так надо"}
    data.update(extra)
    return data


def adapter_data() -> dict:
    with ADAPTER.open("rb") as handle:
        return tomllib.load(handle)


def question(*, id: str = "one", slots=("project.purpose",)) -> Question:
    return Question(id=id, text="Вопрос?", slots=tuple(slots), why="так надо", source="universal")


class QuestionParsingTests(unittest.TestCase):
    def assert_error(self, entries: list, path: str, source: str = "universal") -> None:
        with self.assertRaises(InterviewError) as context:
            parse_questions({"question": entries}, source)
        paths = [error_path for error_path, _ in context.exception.errors]
        self.assertIn(path, paths)

    def test_universal_bank_is_valid(self) -> None:
        questions = load_questions(QUESTIONS)
        self.assertEqual(len(questions), UNIVERSAL_LIMIT)
        slots = [slot for item in questions for slot in item.slots]
        self.assertEqual(len(slots), len(set(slots)))
        for item in questions:
            self.assertTrue(item.example, f"{item.id}: нет example")
            self.assertTrue(item.hint, f"{item.id}: нет hint")

    def test_unknown_field_rejected(self) -> None:
        self.assert_error([entry(default="unknown")], "question[0].default")

    def test_missing_why_rejected(self) -> None:
        item = entry()
        del item["why"]
        self.assert_error([item], "question[0].why")

    def test_slot_outside_schema_rejected(self) -> None:
        self.assert_error([entry(slots=["stack.runtime.version"])], "question[0].slots[0]")

    def test_duplicate_slot_across_questions_rejected(self) -> None:
        self.assert_error([entry(), entry(id="two")], "question[1].slots[0]")

    def test_duplicate_slot_inside_question_rejected(self) -> None:
        self.assert_error([entry(slots=["project.purpose", "project.purpose"])], "question[0].slots[1]")

    def test_duplicate_id_rejected(self) -> None:
        self.assert_error(
            [entry(), entry(slots=["project.boundaries"])],
            "question[1].id",
        )

    def test_universal_limit_enforced(self) -> None:
        slots = ["project.purpose", "project.boundaries", "stack.runtime"]
        entries = [entry(id=f"q{index}", slots=[slots[index % len(slots)]]) for index in range(UNIVERSAL_LIMIT + 1)]
        self.assert_error(entries, "question")

    def test_adapter_limit_enforced(self) -> None:
        entries = [entry(id=f"q{index}") for index in range(ADAPTER_LIMIT + 1)]
        self.assert_error(entries, "question", source="adapter")


class MergeTests(unittest.TestCase):
    def test_fixture_adapter_adds_question(self) -> None:
        universal = load_questions(QUESTIONS)
        adapter = load_adapter_questions(adapter_data())
        bank = merge_questions(universal, adapter)
        self.assertEqual(len(bank.questions), UNIVERSAL_LIMIT + 1)
        self.assertEqual(bank.dropped, ())

    def test_duplicate_id_dropped(self) -> None:
        universal = load_questions(QUESTIONS)
        duplicate = Question("purpose", "Другой?", ("gates.disabled",), "так надо", "adapter")
        bank = merge_questions(universal, [duplicate])
        self.assertEqual(len(bank.questions), UNIVERSAL_LIMIT)
        self.assertEqual(bank.dropped[0].id, "purpose")

    def test_covered_slot_dropped(self) -> None:
        universal = load_questions(QUESTIONS)
        overlapping = Question("extra", "Ещё раз?", ("process.branches",), "так надо", "adapter")
        bank = merge_questions(universal, [overlapping])
        self.assertEqual(len(bank.questions), UNIVERSAL_LIMIT)
        self.assertIn("process.branches", bank.dropped[0].reason)

    def test_merged_limit_enforced(self) -> None:
        universal = load_questions(QUESTIONS)
        extra = [question(id=f"a{index}", slots=(f"custom.{index}",)) for index in range(MERGED_LIMIT)]
        with self.assertRaises(InterviewError) as context:
            merge_questions(universal, extra)
        self.assertIn("question", [error_path for error_path, _ in context.exception.errors])


class ProbeParsingTests(unittest.TestCase):
    def assert_error(self, probes: list, path: str) -> None:
        with self.assertRaises(InterviewError) as context:
            parse_probes(probes)
        paths = [error_path for error_path, _ in context.exception.errors]
        self.assertIn(path, paths)

    def probe(self, **extra) -> dict:
        data = {
            "id": "p",
            "globs": ["manifest.json"],
            "field": "stack.runtime",
            "name": "runtime",
            "extract": "json:runtime.version",
            "provenance": "probe",
        }
        data.update(extra)
        return data

    def test_fixture_probes_are_valid(self) -> None:
        probes = load_probes(adapter_data())
        self.assertEqual([item.id for item in probes], ["runtime-version", "framework-name"])

    def test_absolute_glob_rejected(self) -> None:
        self.assert_error([self.probe(globs=["/etc/manifest.json"])], "probes[0].globs[0]")

    def test_drive_glob_rejected(self) -> None:
        self.assert_error([self.probe(globs=["C:/manifest.json"])], "probes[0].globs[0]")

    def test_backslash_glob_rejected(self) -> None:
        self.assert_error([self.probe(globs=["config\\manifest.json"])], "probes[0].globs[0]")

    def test_parent_glob_rejected(self) -> None:
        self.assert_error([self.probe(globs=["../manifest.json"])], "probes[0].globs[0]")

    def test_field_outside_schema_rejected(self) -> None:
        self.assert_error([self.probe(field="stack.runtime.version")], "probes[0].field")

    def test_evidence_field_requires_name(self) -> None:
        data = self.probe()
        del data["name"]
        self.assert_error([data], "probes[0].name")

    def test_name_allowed_only_for_evidence(self) -> None:
        self.assert_error([self.probe(field="project.purpose")], "probes[0].name")

    def test_unknown_extract_kind_rejected(self) -> None:
        self.assert_error([self.probe(extract="xml:runtime")], "probes[0].extract")

    def test_broken_regex_rejected(self) -> None:
        self.assert_error([self.probe(extract="regex:(")], "probes[0].extract")

    def test_provenance_answer_rejected(self) -> None:
        self.assert_error([self.probe(provenance="answer")], "probes[0].provenance")

    def test_duplicate_probe_id_rejected(self) -> None:
        self.assert_error([self.probe(), self.probe()], "probes[1].id")


class ProbeRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_json_probe_reports_evidence(self) -> None:
        self.write("manifest.json", '{"runtime": {"version": "9.1"}}')
        probe = Probe("runtime-version", ("manifest.json",), "stack.runtime", "json:runtime.version", "runtime")
        hits, misses = run_probes(self.root, [probe])
        self.assertEqual(misses, [])
        self.assertEqual(hits[0].value, "9.1")
        self.assertEqual(hits[0].evidence, "manifest.json")

    def test_toml_probe(self) -> None:
        self.write("config/store.toml", '[engine]\nversion = "7"\n')
        probe = Probe("store", ("config/*.toml",), "stack.stores", "toml:engine.version", "store")
        hits, _ = run_probes(self.root, [probe])
        self.assertEqual(hits[0].value, "7")

    def test_regex_probe_takes_first_group(self) -> None:
        self.write("VERSION", "release = 3.4.5\n")
        probe = Probe("release", ("VERSION",), "project.purpose", r"regex:release = ([\d.]+)", None)
        hits, _ = run_probes(self.root, [probe])
        self.assertEqual(hits[0].value, "3.4.5")

    def test_missing_file_is_miss(self) -> None:
        probe = Probe("runtime-version", ("manifest.json",), "stack.runtime", "json:runtime.version", "runtime")
        _, misses = run_probes(self.root, [probe])
        self.assertIn("не найдены", misses[0].reason)

    def test_missing_value_is_miss(self) -> None:
        self.write("manifest.json", '{"runtime": {}}')
        probe = Probe("runtime-version", ("manifest.json",), "stack.runtime", "json:runtime.version", "runtime")
        _, misses = run_probes(self.root, [probe])
        self.assertIn("значение не найдено", misses[0].reason)

    def test_large_file_is_miss(self) -> None:
        self.write("manifest.json", "x" * (2 * 1024 * 1024 + 1))
        probe = Probe("runtime-version", ("manifest.json",), "stack.runtime", "json:runtime.version", "runtime")
        _, misses = run_probes(self.root, [probe])
        self.assertIn("больше", misses[0].reason)


class ProbeApplyTests(unittest.TestCase):
    def hit(self, *, field: str, value, name=None, evidence="manifest.json"):
        from ai_kit.interview import ProbeHit

        return ProbeHit("p", field, name, value, evidence)

    def test_scalar_hit_sets_slot(self) -> None:
        values = apply_probe_hits({}, [self.hit(field="project.purpose", value="продукт")])
        self.assertEqual(values["project"]["purpose"], "продукт")

    def test_evidence_hit_appends_entry(self) -> None:
        values = apply_probe_hits({}, [self.hit(field="stack.runtime", value="9.1", name="runtime")])
        self.assertEqual(
            values["stack"]["runtime"],
            [{"name": "runtime", "version": "9.1", "evidence": "manifest.json", "provenance": "probe"}],
        )

    def test_two_probes_for_one_fact_rejected(self) -> None:
        with self.assertRaises(InterviewError):
            apply_probe_hits(
                {},
                [
                    self.hit(field="stack.runtime", value="9.1", name="runtime"),
                    self.hit(field="stack.runtime", value="9.2", name="runtime"),
                ],
            )

    def test_probe_replaces_existing_fact(self) -> None:
        values = {
            "stack": {
                "runtime": [
                    {"name": "runtime", "version": "9.0", "evidence": "old", "provenance": "probe"}
                ]
            }
        }
        result = apply_probe_hits(values, [self.hit(field="stack.runtime", value="9.1", name="runtime")])
        self.assertEqual(result["stack"]["runtime"][0]["version"], "9.1")
        self.assertEqual(result["stack"]["runtime"][0]["evidence"], "manifest.json")

    def test_wrong_scalar_type_rejected(self) -> None:
        with self.assertRaises(InterviewError):
            apply_probe_hits({}, [self.hit(field="process.tiers", value="да")])

    def test_two_hits_for_one_scalar_rejected(self) -> None:
        with self.assertRaises(InterviewError):
            apply_probe_hits(
                {},
                [
                    self.hit(field="project.purpose", value="первый"),
                    self.hit(field="project.purpose", value="второй"),
                ],
            )


class AnswerTests(unittest.TestCase):
    def test_scalar_answer_sets_value(self) -> None:
        values, unknowns, stats = apply_answers(
            {}, [question()], [question()], [Answer("project.purpose", value="продукт")]
        )
        self.assertEqual(values["project"]["purpose"], "продукт")
        self.assertEqual(unknowns, [])
        self.assertEqual(stats.answered, 1)
        self.assertEqual(stats.asked, 1)

    def test_unknown_answer_is_reported(self) -> None:
        values, unknowns, _ = apply_answers(
            {}, [question()], [question()], [Answer("project.purpose", status="unknown")]
        )
        self.assertNotIn("project", values)
        self.assertEqual(unknowns[0]["field"], "project.purpose")

    def test_answer_for_known_slot_ignored(self) -> None:
        values = {"project": {"purpose": "из пробы"}}
        result, _, stats = apply_answers(
            values, [question()], [], [Answer("project.purpose", value="из ответа")]
        )
        self.assertEqual(result["project"]["purpose"], "из пробы")
        self.assertEqual(stats.ignored, 1)

    def test_answer_outside_bank_rejected(self) -> None:
        with self.assertRaises(InterviewError):
            apply_answers({}, [question()], [question()], [Answer("layout.source_roots", value=["src"])])

    def test_table_answer_validated(self) -> None:
        item = question(id="tests", slots=("tests.commands",))
        values, _, _ = apply_answers({}, [item], [item], [Answer("tests.commands", value={"unit": "run"})])
        self.assertEqual(values["tests"]["commands"], {"unit": "run"})

    def test_table_answer_requires_fields(self) -> None:
        item = question(id="gates", slots=("gates.disabled",))
        with self.assertRaises(InterviewError):
            apply_answers(
                {},
                [item],
                [item],
                [Answer("gates.disabled", value=[{"id": "layers", "reason": "нет", "owner": "автор"}])],
            )

    def test_evidence_answer_requests_version_proof(self) -> None:
        item = question(id="runtime", slots=("stack.runtime",))
        values, unknowns, _ = apply_answers(
            {},
            [item],
            [item],
            [Answer("stack.runtime", name="runtime", version="9")],
        )
        self.assertEqual(values["stack"]["runtime"], [{"name": "runtime", "provenance": "answer"}])
        self.assertEqual(unknowns[0]["field"], "stack.runtime.version")

    def test_evidence_answer_requires_name(self) -> None:
        item = question(id="runtime", slots=("stack.runtime",))
        with self.assertRaises(InterviewError):
            apply_answers({}, [item], [item], [Answer("stack.runtime")])

    def test_multiple_evidence_answers(self) -> None:
        item = question(id="stores", slots=("stack.stores",))
        parsed = parse_answers(
            {
                "answer": [
                    {"slot": "stack.stores", "name": "first"},
                    {"slot": "stack.stores", "name": "second"},
                ]
            }
        )
        values, _, stats = apply_answers({}, [item], [item], parsed)
        self.assertEqual([entry["name"] for entry in values["stack"]["stores"]], ["first", "second"])
        self.assertEqual(stats.answered, 2)

    def test_duplicate_evidence_name_rejected(self) -> None:
        with self.assertRaises(InterviewError):
            parse_answers(
                {
                    "answer": [
                        {"slot": "stack.stores", "name": "same"},
                        {"slot": "stack.stores", "name": "same"},
                    ]
                }
            )

    def test_evidence_answer_without_version_requests_proof(self) -> None:
        item = question(id="runtime", slots=("stack.runtime",))
        _, unknowns, _ = apply_answers({}, [item], [item], [Answer("stack.runtime", name="runtime")])
        self.assertEqual(unknowns[0]["field"], "stack.runtime.version")


class AnswerParsingTests(unittest.TestCase):
    def assert_error(self, entries: list, path: str) -> None:
        with self.assertRaises(InterviewError) as context:
            parse_answers({"answer": entries})
        paths = [error_path for error_path, _ in context.exception.errors]
        self.assertIn(path, paths)

    def test_scalar_answer_valid(self) -> None:
        answers = parse_answers({"answer": [{"slot": "project.purpose", "value": "продукт"}]})
        self.assertEqual(answers[0].value, "продукт")

    def test_unknown_answer_valid(self) -> None:
        answers = parse_answers({"answer": [{"slot": "project.purpose", "status": "unknown"}]})
        self.assertEqual(answers[0].status, "unknown")

    def test_unknown_answer_with_value_rejected(self) -> None:
        self.assert_error(
            [{"slot": "project.purpose", "status": "unknown", "value": "x"}],
            "answer[0]",
        )

    def test_answer_without_value_rejected(self) -> None:
        self.assert_error([{"slot": "project.purpose"}], "answer[0].value")

    def test_slot_outside_schema_rejected(self) -> None:
        self.assert_error([{"slot": "stack.runtime.version", "value": "1"}], "answer[0].slot")

    def test_duplicate_slot_rejected(self) -> None:
        self.assert_error(
            [
                {"slot": "project.purpose", "value": "a"},
                {"slot": "project.purpose", "value": "b"},
            ],
            "answer[1].slot",
        )

    def test_evidence_answer_rejects_value(self) -> None:
        self.assert_error(
            [{"slot": "stack.runtime", "name": "runtime", "value": "9"}],
            "answer[0].value",
        )

    def test_scalar_answer_rejects_name(self) -> None:
        self.assert_error(
            [{"slot": "project.purpose", "value": "x", "name": "runtime"}],
            "answer[0]",
        )

    def test_json_answers_file(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "answers.json"
            path.write_text(
                json.dumps({"answer": [{"slot": "project.purpose", "value": "продукт"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            answers = load_answers(path)
        self.assertEqual(answers[0].value, "продукт")


class FinishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_probe_miss_resolved_by_answer_is_not_reported(self) -> None:
        (self.root / "manifest.json").write_text("{}", encoding="utf-8")
        probe = Probe("runtime-version", ("manifest.json",), "stack.runtime", "json:runtime.version", "runtime")
        item = question(id="runtime", slots=("stack.runtime",))
        bank = merge_questions([], [item])
        prepared = prepare({}, self.root, [probe], bank)
        result = finish(prepared, [Answer("stack.runtime", name="runtime", version="9")])
        fields = [entry["field"] for entry in result.unknowns]
        self.assertNotIn("stack.runtime", fields)
        self.assertIn("stack.runtime.version", fields)

    def test_report_counts_facts(self) -> None:
        item = question()
        bank = merge_questions([], [item])
        prepared = prepare({}, self.root, [], bank)
        result = finish(prepared, [Answer("project.purpose", value="продукт")])
        text = report(result)
        self.assertIn("| фактов получено ответом (`answer`) | 0 |", text)
        self.assertIn("| вопросов задано | 1 |", text)

    def test_report_names_unknowns(self) -> None:
        item = question()
        bank = merge_questions([], [item])
        result = finish(prepare({}, self.root, [], bank), [])
        self.assertIn("`project.purpose`", report(result))


class KnownTests(unittest.TestCase):
    def test_missing_and_empty_values_are_unknown(self) -> None:
        self.assertFalse(is_known({}, "project.purpose"))
        self.assertFalse(is_known({"project": {"boundaries": []}}, "project.boundaries"))
        self.assertTrue(is_known({"process": {"tiers": False}}, "process.tiers"))


class PlanJsonTests(unittest.TestCase):
    def prepared(self) -> Prepared:
        item = Question(
            id="stack",
            text="Какой стек?",
            slots=("stack.runtime", "tests.commands"),
            why="нужно",
            source="universal",
            example="Пример ответа",
            hint="Формат ответа",
        )
        return Prepared(values={}, questions=(item,), batch=(item,), hits=(), misses=())

    def test_slots_are_described(self) -> None:
        payload = plan_payload(self.prepared())
        slots = payload["questions"][0]["slots"]
        self.assertEqual(slots[0]["path"], "stack.runtime")
        self.assertTrue(slots[0]["evidence"])
        self.assertEqual(slots[1]["type"], "object")
        self.assertEqual(slots[1]["additional"], "string")
        self.assertEqual(payload["questions"][0]["example"], "Пример ответа")
        self.assertEqual(payload["questions"][0]["hint"], "Формат ответа")

    def test_describe_unknown_slot(self) -> None:
        self.assertEqual(describe_slot("project.missing"), {"path": "project.missing", "type": "unknown"})


class CliTests(unittest.TestCase):
    def test_check_works_from_project_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            output = io.StringIO()
            with patch("pathlib.Path.cwd", return_value=Path(temporary)), redirect_stdout(output):
                code = interview_main(["--check", "--adapter", str(ADAPTER)])
        self.assertEqual(code, 0)
        self.assertIn("Вопросов:", output.getvalue())

    def test_plan_uses_kit_questions(self) -> None:
        with TemporaryDirectory() as temporary:
            output = io.StringIO()
            with (
                patch("pathlib.Path.cwd", return_value=Path(temporary)),
                redirect_stdout(output),
                redirect_stderr(io.StringIO()),
            ):
                code = interview_main(
                    ["--plan", "--profile", str(EXAMPLE), "--adapter", str(ADAPTER), "--json"]
                )
        self.assertEqual(code, 0)
        self.assertIn('"questions"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
