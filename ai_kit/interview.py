"""Интервью и пробы: банк вопросов, дедупликация, пробы, ответы и отчёт."""

from __future__ import annotations

import copy
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_kit import schema
from ai_kit.args import UsageError, ensure_known, normalize
from ai_kit.console import configure_console
from ai_kit.paths import KitRootError, kit_root
from ai_kit.templates import COMMAND_ROLES
from ai_kit.toml_io import TomlError, load as load_toml

UNIVERSAL_LIMIT = 12
ADAPTER_LIMIT = 5
MERGED_LIMIT = 15
MAX_PROBE_BYTES = 2 * 1024 * 1024

ANSWER_STATUSES = ("answered", "unknown")
EXTRACT_KINDS = ("json", "toml", "regex", "none")
EXTRACT_PATH = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
DRIVE = re.compile(r"^[A-Za-z]:")

ABSENT = object()


class InterviewError(Exception):
    def __init__(self, errors: list[tuple[str, str]]) -> None:
        self.errors = errors
        super().__init__("\n".join(f"{path}: {message}" for path, message in errors))


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    slots: tuple[str, ...]
    why: str
    source: str
    example: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class Probe:
    id: str
    globs: tuple[str, ...]
    field: str
    extract: str
    name: str | None
    provenance: str = "probe"
    version_required: bool = True


@dataclass(frozen=True)
class ProbeHit:
    probe_id: str
    field: str
    name: str | None
    value: Any
    evidence: str


@dataclass(frozen=True)
class ProbeMiss:
    probe_id: str
    field: str
    reason: str


@dataclass(frozen=True)
class Answer:
    slot: str
    status: str = "answered"
    value: Any = ABSENT
    name: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class AnswerStats:
    asked: int
    answered: int
    ignored: int


@dataclass(frozen=True)
class DroppedQuestion:
    id: str
    reason: str


@dataclass(frozen=True)
class Bank:
    questions: tuple[Question, ...]
    dropped: tuple[DroppedQuestion, ...]


@dataclass(frozen=True)
class Prepared:
    values: dict[str, Any]
    questions: tuple[Question, ...]
    batch: tuple[Question, ...]
    hits: tuple[ProbeHit, ...]
    misses: tuple[ProbeMiss, ...]


@dataclass(frozen=True)
class InterviewResult:
    values: dict[str, Any]
    unknowns: tuple[dict[str, str], ...]
    batch: tuple[Question, ...]
    hits: tuple[ProbeHit, ...]
    misses: tuple[ProbeMiss, ...]
    stats: AnswerStats


def load_questions(path: Path, source: str = "universal") -> list[Question]:
    return parse_questions(load_toml(path), source)


def parse_questions(data: dict[str, Any], source: str) -> list[Question]:
    errors: list[tuple[str, str]] = []
    limit = UNIVERSAL_LIMIT if source == "universal" else ADAPTER_LIMIT
    entries = data.get("question")
    if not isinstance(entries, list):
        raise InterviewError([("question", "ожидается список вопросов")])
    if len(entries) > limit:
        errors.append(("question", f"вопросов {len(entries)}, допустимо не больше {limit}"))

    questions: list[Question] = []
    seen_ids: set[str] = set()
    seen_slots: set[str] = set()
    for index, entry in enumerate(entries):
        place = f"question[{index}]"
        if not isinstance(entry, dict):
            errors.append((place, "ожидается объект"))
            continue
        for key in sorted(set(entry) - {"id", "question", "slots", "why", "example", "hint"}):
            errors.append((f"{place}.{key}", "неизвестное поле"))

        fields: dict[str, str] = {}
        for key in ("id", "question", "why"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append((f"{place}.{key}", "ожидается непустая строка"))
            else:
                fields[key] = value
        for key in ("example", "hint"):
            value = entry.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                errors.append((f"{place}.{key}", "ожидается непустая строка"))
            elif isinstance(value, str):
                fields[key] = value

        slots = entry.get("slots")
        if not isinstance(slots, list) or not slots or not all(
            isinstance(slot, str) and slot for slot in slots
        ):
            errors.append((f"{place}.slots", "ожидается непустой список путей"))
            slots = []

        own_slots: set[str] = set()
        for slot_index, slot in enumerate(slots):
            if schema.resolve(slot) is None:
                errors.append((f"{place}.slots[{slot_index}]", "поля нет в схеме профиля"))
            if slot in own_slots:
                errors.append((f"{place}.slots[{slot_index}]", "слот повторяется в вопросе"))
            elif slot in seen_slots:
                errors.append((f"{place}.slots[{slot_index}]", "слот задан другим вопросом"))
            own_slots.add(slot)
        seen_slots.update(own_slots)

        if any(key not in fields for key in ("id", "question", "why")) or not slots:
            continue
        if fields["id"] in seen_ids:
            errors.append((f"{place}.id", "идентификатор повторяется"))
        seen_ids.add(fields["id"])
        questions.append(
            Question(
                id=fields["id"],
                text=fields["question"],
                slots=tuple(slots),
                why=fields["why"],
                source=source,
                example=fields.get("example"),
                hint=fields.get("hint"),
            )
        )

    if errors:
        raise InterviewError(errors)
    return questions


def load_adapter_questions(adapter: dict[str, Any]) -> list[Question]:
    return parse_questions({"question": adapter.get("questions", [])}, "adapter")


def merge_questions(universal: list[Question], adapter: list[Question]) -> Bank:
    errors: list[tuple[str, str]] = []
    questions: list[Question] = []
    seen_ids: set[str] = set()
    covered: set[str] = set()
    for question in universal:
        overlap = covered & set(question.slots)
        if overlap:
            errors.append((f"question.{question.id}", f"слоты заданы дважды: {', '.join(sorted(overlap))}"))
        covered.update(question.slots)
        seen_ids.add(question.id)
        questions.append(question)

    dropped: list[DroppedQuestion] = []
    for question in adapter:
        if question.id in seen_ids:
            dropped.append(DroppedQuestion(question.id, "идентификатор уже есть"))
            continue
        overlap = covered & set(question.slots)
        if overlap:
            dropped.append(DroppedQuestion(question.id, f"слоты уже закрыты: {', '.join(sorted(overlap))}"))
            continue
        covered.update(question.slots)
        seen_ids.add(question.id)
        questions.append(question)

    if len(questions) > MERGED_LIMIT:
        errors.append(("question", f"после дедупликации {len(questions)}, допустимо не больше {MERGED_LIMIT}"))
    if errors:
        raise InterviewError(errors)
    return Bank(tuple(questions), tuple(dropped))


def apply_commands(values: dict[str, Any], adapter: dict[str, Any]) -> dict[str, Any]:
    entries = adapter.get("commands", [])
    if not entries:
        return values
    if not isinstance(entries, list):
        raise InterviewError([("commands", "ожидается список")])
    result = copy.deepcopy(values)
    tests = result.setdefault("tests", {})
    if not isinstance(tests, dict):
        raise InterviewError([("tests", "ожидается объект")])
    commands = tests.setdefault("commands", {})
    if not isinstance(commands, dict):
        raise InterviewError([("tests.commands", "ожидается объект")])

    seen: set[str] = set()
    for position, entry in enumerate(entries):
        place = f"commands[{position}]"
        if not isinstance(entry, dict):
            raise InterviewError([(place, "ожидается объект")])
        extra = set(entry) - {"role", "command"}
        if extra:
            raise InterviewError([(place, f"неизвестные поля: {', '.join(sorted(extra))}")])
        role = entry.get("role")
        command = entry.get("command")
        if role not in COMMAND_ROLES:
            raise InterviewError([(f"{place}.role", f"ожидается одно из: {', '.join(COMMAND_ROLES)}")])
        if not isinstance(command, str) or not command.strip():
            raise InterviewError([(f"{place}.command", "ожидается непустая строка")])
        if role in seen:
            raise InterviewError([(f"{place}.role", "роль повторяется")])
        seen.add(role)
        commands.setdefault(role, command)
    return result


def load_probes(adapter: dict[str, Any]) -> list[Probe]:
    return parse_probes(adapter.get("probes", []))


def parse_probes(entries: Any) -> list[Probe]:
    if not isinstance(entries, list):
        raise InterviewError([("probes", "ожидается список проб")])

    errors: list[tuple[str, str]] = []
    probes: list[Probe] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        place = f"probes[{index}]"
        if not isinstance(entry, dict):
            errors.append((place, "ожидается объект"))
            continue
        for key in sorted(set(entry) - {"id", "globs", "field", "extract", "name", "provenance", "version"}):
            errors.append((f"{place}.{key}", "неизвестное поле"))

        probe_id = entry.get("id")
        if not isinstance(probe_id, str) or not probe_id.strip():
            errors.append((f"{place}.id", "ожидается непустая строка"))
            probe_id = ""
        elif probe_id in seen_ids:
            errors.append((f"{place}.id", "идентификатор повторяется"))
        seen_ids.add(probe_id)

        globs = entry.get("globs")
        if not isinstance(globs, list) or not globs or not all(
            isinstance(item, str) and item for item in globs
        ):
            errors.append((f"{place}.globs", "ожидается непустой список масок"))
            globs = []
        for glob_index, glob in enumerate(globs):
            reason = _glob_problem(glob)
            if reason:
                errors.append((f"{place}.globs[{glob_index}]", reason))

        field_name = entry.get("field")
        node = None
        if not isinstance(field_name, str) or not field_name:
            errors.append((f"{place}.field", "ожидается непустая строка"))
        else:
            node = schema.resolve(field_name)
            if node is None:
                errors.append((f"{place}.field", "поля нет в схеме профиля"))

        name = entry.get("name")
        if schema.is_evidence(node):
            if not isinstance(name, str) or not name.strip():
                errors.append((f"{place}.name", "для поля-доказательства нужно имя факта"))
        elif name is not None:
            errors.append((f"{place}.name", "имя допустимо только для поля-доказательства"))

        version = entry.get("version", "probe")
        if version not in ("probe", "none"):
            errors.append((f"{place}.version", "ожидается probe или none"))
        if version == "none" and not schema.is_evidence(node):
            errors.append((f"{place}.version", "none допустим только для поля-доказательства"))

        extract = entry.get("extract")
        if version == "none" and extract is None:
            extract = "none"
        if not isinstance(extract, str) or not extract:
            errors.append((f"{place}.extract", "ожидается правило извлечения"))
        else:
            _validate_extract(extract, f"{place}.extract", errors)
        if version == "none" and extract != "none":
            errors.append((f"{place}.extract", "при version = none значение не извлекается"))

        if entry.get("provenance") != "probe":
            errors.append((f"{place}.provenance", "проба всегда даёт probe"))

        if probe_id and globs and node is not None and isinstance(extract, str):
            probes.append(
                Probe(
                    id=probe_id,
                    globs=tuple(globs),
                    field=field_name,
                    extract=extract,
                    name=name if isinstance(name, str) else None,
                    version_required=version != "none",
                )
            )

    if errors:
        raise InterviewError(errors)
    return probes


def _glob_problem(glob: str) -> str | None:
    if "\\" in glob:
        return "маска использует обратный слэш: нужен прямой"
    if glob.startswith("/") or DRIVE.match(glob):
        return "маска должна быть относительной"
    if ".." in glob.split("/"):
        return "маска не может выходить вверх через .."
    return None


def _validate_extract(extract: str, path: str, errors: list[tuple[str, str]]) -> None:
    if extract == "none":
        return
    kind, separator, argument = extract.partition(":")
    if not separator or kind not in EXTRACT_KINDS:
        errors.append((path, f"ожидается одно из: {', '.join(EXTRACT_KINDS)}"))
        return
    if kind == "none":
        if argument:
            errors.append((path, "none не принимает аргумент"))
        return
    if not argument:
        errors.append((path, "правило извлечения пустое"))
        return
    if kind == "regex":
        try:
            re.compile(argument)
        except re.error as error:
            errors.append((path, f"регулярное выражение не разбирается: {error}"))
    elif EXTRACT_PATH.fullmatch(argument) is None:
        errors.append((path, "путь задаётся точками: латинские буквы, цифры, дефис, подчёркивание"))


def run_probes(root: Path, probes: list[Probe]) -> tuple[list[ProbeHit], list[ProbeMiss]]:
    hits: list[ProbeHit] = []
    misses: list[ProbeMiss] = []
    for probe in probes:
        result = _run_probe(root, probe)
        if isinstance(result, ProbeHit):
            hits.append(result)
        else:
            misses.append(result)
    return hits, misses


def _run_probe(root: Path, probe: Probe) -> ProbeHit | ProbeMiss:
    reason = "файлы не найдены"
    for pattern in probe.globs:
        try:
            matches = sorted(root.glob(pattern), key=lambda path: path.as_posix())
        except (OSError, ValueError, NotImplementedError) as error:
            reason = f"маска не разбирается: {error}"
            continue
        for path in matches:
            if not path.is_file():
                continue
            if not _inside(path, root):
                reason = f"файл вне проекта: {path}"
                continue
            try:
                if path.stat().st_size > MAX_PROBE_BYTES:
                    reason = f"файл больше {MAX_PROBE_BYTES} байт"
                    continue
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as error:
                reason = f"файл не читается: {error}"
                continue
            if not probe.version_required:
                return ProbeHit(
                    probe_id=probe.id,
                    field=probe.field,
                    name=probe.name,
                    value=None,
                    evidence=path.relative_to(root).as_posix(),
                )
            value = _extract(probe.extract, text)
            if value is ABSENT:
                reason = "значение не найдено"
                continue
            return ProbeHit(
                probe_id=probe.id,
                field=probe.field,
                name=probe.name,
                value=value,
                evidence=path.relative_to(root).as_posix(),
            )
    return ProbeMiss(probe.id, probe.field, reason)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _extract(extract: str, text: str) -> Any:
    kind, _, argument = extract.partition(":")
    if kind == "regex":
        match = re.search(argument, text, re.MULTILINE)
        if match is None:
            return ABSENT
        return match.group(1) if match.groups() else match.group(0)
    try:
        data = json.loads(text) if kind == "json" else tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError):
        return ABSENT
    node: Any = data
    for part in argument.split("."):
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return ABSENT
    return node


def apply_probe_hits(values: dict[str, Any], hits: list[ProbeHit]) -> dict[str, Any]:
    result = copy.deepcopy(values)
    filled: set[str] = set()
    facts: set[tuple[str, str]] = set()
    for hit in hits:
        node = schema.resolve(hit.field)
        if node is None:
            raise InterviewError([(hit.field, "поля нет в схеме профиля")])
        if schema.is_evidence(node):
            if hit.name is None:
                raise InterviewError([(hit.field, "для поля-доказательства нужно имя факта")])
            if (hit.field, hit.name) in facts:
                raise InterviewError([(hit.field, f"две пробы на факт {hit.name}")])
            facts.add((hit.field, hit.name))
            if hit.value is not None and (
                isinstance(hit.value, bool) or not isinstance(hit.value, (str, int, float))
            ):
                raise InterviewError([(hit.field, "версия должна быть строкой или числом")])
            entry = {
                "name": hit.name,
                "evidence": hit.evidence,
                "provenance": "probe",
            }
            if hit.value is not None:
                entry["version"] = str(hit.value)
            _upsert_evidence(result, hit.field, entry)
            continue
        if hit.field in filled:
            raise InterviewError([(hit.field, "значение уже заполнено другой пробой")])
        validate_value(node, hit.value, hit.field)
        _set_slot(result, hit.field, hit.value)
        filled.add(hit.field)
    return result


def _upsert_evidence(values: dict[str, Any], field_name: str, entry: dict[str, str]) -> None:
    existing = _get_slot(values, field_name)
    if existing is ABSENT:
        _set_slot(values, field_name, [entry])
        return
    if not isinstance(existing, list):
        raise InterviewError([(field_name, "ожидается список доказательств")])
    for index, item in enumerate(existing):
        if isinstance(item, dict) and item.get("name") == entry["name"]:
            existing[index] = entry
            return
    existing.append(entry)


def _append_evidence(values: dict[str, Any], field_name: str, entry: dict[str, str]) -> None:
    existing = _get_slot(values, field_name)
    if existing is ABSENT:
        _set_slot(values, field_name, [entry])
        return
    if not isinstance(existing, list):
        raise InterviewError([(field_name, "ожидается список доказательств")])
    for item in existing:
        if isinstance(item, dict) and item.get("name") == entry["name"]:
            raise InterviewError([(field_name, f"два значения для {entry['name']}")])
    existing.append(entry)


def plan_batch(questions: list[Question], values: dict[str, Any]) -> list[Question]:
    return [question for question in questions if any(not is_known(values, slot) for slot in question.slots)]


def load_answers(path: Path) -> list[Answer]:
    if path.suffix == ".json":
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as error:
            raise InterviewError([(str(path), f"файл не читается: {error}")]) from error
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise InterviewError([(str(path), f"JSON не разбирается: {error}")]) from error
    else:
        data = load_toml(path)
    return parse_answers(data)


def describe_slot(path: str) -> dict[str, Any]:
    node = schema.resolve(path)
    if node is None:
        return {"path": path, "type": "unknown"}
    kind = node.get("type", "unknown")
    data: dict[str, Any] = {"path": path, "type": kind}
    if schema.is_evidence(node):
        data["evidence"] = True
        data["answer"] = "name"
    elif kind == "array":
        data["items"] = node.get("items", {}).get("type", "unknown")
    elif kind == "object":
        properties = node.get("properties", {})
        data["properties"] = {key: value.get("type", "unknown") for key, value in properties.items()}
        if node.get("required"):
            data["required"] = list(node["required"])
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            data["additional"] = additional.get("type", "unknown")
    return data


def plan_payload(prepared: Prepared) -> dict[str, Any]:
    return {
        "probes": {
            "hits": [
                {
                    "probe": hit.probe_id,
                    "field": hit.field,
                    "name": hit.name,
                    "value": hit.value,
                    "evidence": hit.evidence,
                }
                for hit in prepared.hits
            ],
            "misses": [
                {"probe": miss.probe_id, "field": miss.field, "reason": miss.reason}
                for miss in prepared.misses
            ],
        },
        "questions": [
            {
                "id": question.id,
                "text": question.text,
                "why": question.why,
                "example": question.example,
                "hint": question.hint,
                "source": question.source,
                "slots": [describe_slot(slot) for slot in question.slots],
            }
            for question in prepared.batch
        ],
    }


def plan_json(prepared: Prepared) -> str:
    return json.dumps(plan_payload(prepared), ensure_ascii=False, indent=2)


def parse_answers(data: dict[str, Any]) -> list[Answer]:
    entries = data.get("answer")
    if not isinstance(entries, list):
        raise InterviewError([("answer", "ожидается список ответов")])

    errors: list[tuple[str, str]] = []
    answers: list[Answer] = []
    seen_slots: set[str] = set()
    seen_facts: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        place = f"answer[{index}]"
        if not isinstance(entry, dict):
            errors.append((place, "ожидается объект"))
            continue
        for key in sorted(set(entry) - {"slot", "status", "value", "name", "version"}):
            errors.append((f"{place}.{key}", "неизвестное поле"))

        slot = entry.get("slot")
        name = entry.get("name")
        node = None
        if not isinstance(slot, str) or not slot:
            errors.append((f"{place}.slot", "ожидается непустой путь"))
            slot = ""
        else:
            node = schema.resolve(slot)
            if node is None:
                errors.append((f"{place}.slot", "поля нет в схеме профиля"))
            if slot in seen_slots:
                allowed = (
                    schema.is_evidence(node)
                    and entry.get("status", "answered") == "answered"
                    and isinstance(name, str)
                    and bool(name.strip())
                    and (slot, name) not in seen_facts
                )
                if not allowed:
                    errors.append((f"{place}.slot", "слот отвечен дважды"))
            seen_slots.add(slot)
            if schema.is_evidence(node) and isinstance(name, str) and name.strip():
                seen_facts.add((slot, name))

        status = entry.get("status", "answered")
        if status not in ANSWER_STATUSES:
            errors.append((f"{place}.status", f"ожидается {', '.join(ANSWER_STATUSES)}"))
            status = "answered"

        version = entry.get("version")
        if status == "unknown":
            if "value" in entry or name is not None or version is not None:
                errors.append((place, "у неизвестного ответа не бывает значения"))
            if slot:
                answers.append(Answer(slot=slot, status="unknown"))
            continue

        if not slot:
            continue
        if schema.is_evidence(node):
            if "value" in entry:
                errors.append((f"{place}.value", "факт о стеке отвечается именем, версия — только из пробы"))
            if not isinstance(name, str) or not name.strip():
                errors.append((f"{place}.name", "для поля-доказательства нужно имя факта"))
            if isinstance(version, str) and not version.strip():
                errors.append((f"{place}.version", "ожидается непустая строка"))
            if version is not None and not isinstance(version, str):
                errors.append((f"{place}.version", "ожидается строка"))
            answers.append(
                Answer(
                    slot=slot,
                    name=name if isinstance(name, str) else None,
                    version=version if isinstance(version, str) else None,
                )
            )
            continue

        if name is not None or version is not None:
            errors.append((place, "имя и версия допустимы только для поля-доказательства"))
        if "value" not in entry:
            errors.append((f"{place}.value", "у ответа нет значения"))
            continue
        answers.append(Answer(slot=slot, value=entry["value"]))

    if errors:
        raise InterviewError(errors)
    return answers


def apply_answers(
    values: dict[str, Any],
    questions: list[Question],
    batch: list[Question],
    answers: list[Answer],
) -> tuple[dict[str, Any], list[dict[str, str]], AnswerStats]:
    result = copy.deepcopy(values)
    slot_question = {slot: question for question in questions for slot in question.slots}
    unknowns: list[dict[str, str]] = []
    answered = 0
    ignored = 0

    for answer in answers:
        question = slot_question.get(answer.slot)
        if question is None:
            raise InterviewError([(answer.slot, "слот не задан ни одним вопросом банка")])
        node = schema.resolve(answer.slot)
        if node is None:
            raise InterviewError([(answer.slot, "поля нет в схеме профиля")])
        evidence = schema.is_evidence(node)
        known = is_known(result, answer.slot)
        if known:
            names = []
            if evidence and answer.status == "answered" and answer.name:
                current = _get_slot(result, answer.slot)
                if isinstance(current, list):
                    names = [item.get("name") for item in current if isinstance(item, dict)]
            if not evidence or answer.status != "answered" or answer.name in names:
                ignored += 1
                continue
        if answer.status == "unknown":
            unknowns.append({"field": answer.slot, "question": question.text})
            continue

        if evidence:
            if not answer.name:
                raise InterviewError([(answer.slot, "для поля-доказательства нужно имя факта")])
            _append_evidence(result, answer.slot, {"name": answer.name, "provenance": "answer"})
            answered += 1
            if answer.version == "none":
                continue
            if answer.version:
                question_text = f"Версия {answer.name} названа по памяти; подтвердите файлом-доказательством."
            else:
                question_text = f"Версия {answer.name} не подтверждена; укажите файл-доказательство."
            unknowns.append({"field": f"{answer.slot}.version", "question": question_text})
            continue

        validate_value(node, answer.value, answer.slot)
        _set_slot(result, answer.slot, answer.value)
        answered += 1

    known_unknowns = {entry["field"] for entry in unknowns}
    for question in batch:
        for slot in question.slots:
            if not is_known(result, slot) and slot not in known_unknowns:
                unknowns.append({"field": slot, "question": question.text})
                known_unknowns.add(slot)

    return result, unknowns, AnswerStats(asked=len(batch), answered=answered, ignored=ignored)


def probe_unknowns(misses: list[ProbeMiss]) -> list[dict[str, str]]:
    return [
        {
            "field": miss.field,
            "question": f"Проба «{miss.probe_id}» не нашла значение: {miss.reason}. Укажите файл-доказательство.",
        }
        for miss in misses
    ]


def dedupe_unknowns(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result = []
    for entry in entries:
        if entry["field"] in seen:
            continue
        seen.add(entry["field"])
        result.append(entry)
    return result


def prepare(values: dict[str, Any], root: Path, probes: list[Probe], bank: Bank) -> Prepared:
    hits, misses = run_probes(root, probes)
    filled = apply_probe_hits(values, hits)
    return Prepared(
        values=filled,
        questions=bank.questions,
        batch=tuple(plan_batch(list(bank.questions), filled)),
        hits=tuple(hits),
        misses=tuple(misses),
    )


def finish(prepared: Prepared, answers: list[Answer]) -> InterviewResult:
    values, unknowns, stats = apply_answers(
        prepared.values,
        list(prepared.questions),
        list(prepared.batch),
        answers,
    )
    merged = dedupe_unknowns(probe_unknowns(list(prepared.misses)) + unknowns)
    unresolved = [entry for entry in merged if not is_known(values, entry["field"])]
    return InterviewResult(
        values=values,
        unknowns=tuple(unresolved),
        batch=prepared.batch,
        hits=prepared.hits,
        misses=prepared.misses,
        stats=stats,
    )


def report(result: InterviewResult) -> str:
    probe_facts = _count_provenance(result.values, "probe")
    answer_facts = _count_provenance(result.values, "answer")
    disabled = result.values.get("gates", {}).get("disabled", [])
    lines = [
        "| Что | Сколько |",
        "|---|---|",
        f"| фактов подтверждено (`probe`) | {probe_facts} |",
        f"| фактов получено ответом (`answer`) | {answer_facts} |",
        f"| вопросов задано | {result.stats.asked} |",
        f"| ответов получено | {result.stats.answered} |",
        f"| ответов проигнорировано (уже подтверждено) | {result.stats.ignored} |",
        f"| осталось неизвестным | {len(result.unknowns)} |",
        f"| отключённых гейтов с триггером | {len(disabled)} |",
    ]
    if result.unknowns:
        fields = ", ".join(f"`{entry['field']}`" for entry in result.unknowns)
        lines.append("")
        lines.append(f"Не подтверждено: {fields}.")
    return "\n".join(lines)


def is_known(values: dict[str, Any], path: str) -> bool:
    value = _get_slot(values, path)
    if value is ABSENT or value is None:
        return False
    if isinstance(value, (str, list, dict)) and not value:
        return False
    return True


def _get_slot(values: dict[str, Any], path: str) -> Any:
    node: Any = values
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return ABSENT
        node = node[part]
    return node


def _set_slot(values: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = values
    for part in parts[:-1]:
        child = node.get(part)
        if child is None:
            child = {}
            node[part] = child
        if not isinstance(child, dict):
            raise InterviewError([(path, f"путь проходит через {part}, а это не объект")])
        node = child
    node[parts[-1]] = value


def validate_value(node: dict[str, Any], value: Any, path: str) -> None:
    kind = node.get("type")
    if kind == "array":
        if not isinstance(value, list):
            raise InterviewError([(path, "ожидается список")])
        item = node.get("items", {})
        for index, element in enumerate(value):
            validate_value(item, element, f"{path}[{index}]")
        return
    if kind == "object":
        if not isinstance(value, dict):
            raise InterviewError([(path, "ожидается объект")])
        properties = node.get("properties", {})
        additional = node.get("additionalProperties")
        for key in node.get("required", []):
            if key not in value:
                raise InterviewError([(f"{path}.{key}", "обязательное поле не заполнено")])
        for key, element in value.items():
            if key in properties:
                validate_value(properties[key], element, f"{path}.{key}")
            elif isinstance(additional, dict):
                validate_value(additional, element, f"{path}.{key}")
            else:
                raise InterviewError([(f"{path}.{key}", "неизвестное поле")])
        return
    if kind == "string":
        if not isinstance(value, str) or not value.strip():
            raise InterviewError([(path, "ожидается непустая строка")])
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            raise InterviewError([(path, "ожидается true или false")])
        return
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise InterviewError([(path, "ожидается целое число")])
        return
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InterviewError([(path, "ожидается число")])
        return
    raise InterviewError([(path, "неизвестный тип в схеме профиля")])


def _count_provenance(value: Any, provenance: str) -> int:
    if isinstance(value, dict):
        own = 1 if value.get("provenance") == provenance else 0
        return own + sum(_count_provenance(item, provenance) for item in value.values())
    if isinstance(value, list):
        return sum(_count_provenance(item, provenance) for item in value)
    return 0


def _option(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    return args[index + 1] if index + 1 < len(args) else ""


def _load_adapter(path: str) -> dict[str, Any]:
    return load_toml(Path(path))


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = normalize(sys.argv[1:] if argv is None else argv)
    try:
        ensure_known(
            args,
            {"--check", "--plan", "--report", "--json", "--profile", "--adapter", "--answers", "--project", "--help"},
        )
    except UsageError as error:
        print(f"{error}\nИспользование: python -m ai_kit.interview --help", file=sys.stderr)
        return 2
    if "--help" in args:
        print(__doc__)
        return 0
    try:
        root = kit_root()
    except KitRootError as error:
        print(error, file=sys.stderr)
        return 2
    project_option = _option(args, "--project")
    if project_option == "":
        print("--project требует путь", file=sys.stderr)
        return 2
    project_root = Path(project_option) if project_option else Path.cwd()

    if "--check" in args:
        adapter_path = _option(args, "--adapter")
        if adapter_path == "":
            print("--adapter требует путь", file=sys.stderr)
            return 2
        try:
            universal = load_questions(root / "interview" / "questions.toml")
            adapter_questions: list[Question] = []
            adapter_probes: list[Probe] = []
            if adapter_path:
                adapter_data = _load_adapter(adapter_path)
                adapter_questions = load_adapter_questions(adapter_data)
                adapter_probes = load_probes(adapter_data)
            bank = merge_questions(universal, adapter_questions)
        except (OSError, TomlError, InterviewError) as error:
            print(f"банк вопросов не проходит проверку: {error}", file=sys.stderr)
            return 1
        dropped = ", ".join(item.id for item in bank.dropped) or "нет"
        print(f"Вопросов: {len(bank.questions)} (универсальных {len(universal)}, адаптерных {len(adapter_questions)}).")
        print(f"Проб: {len(adapter_probes)}. Снято дедупликацией: {dropped}.")
        return 0

    if "--plan" in args or "--report" in args:
        profile_path = _option(args, "--profile")
        adapter_path = _option(args, "--adapter")
        if not profile_path or not adapter_path:
            print("нужны --profile <путь> и --adapter <путь>", file=sys.stderr)
            return 2
        try:
            values = load_toml(Path(profile_path))
            adapter_data = _load_adapter(adapter_path)
            values = apply_commands(values, adapter_data)
            universal = load_questions(root / "interview" / "questions.toml")
            bank = merge_questions(universal, load_adapter_questions(adapter_data))
            prepared = prepare(values, project_root, load_probes(adapter_data), bank)
        except (OSError, TomlError, InterviewError) as error:
            print(f"интервью не построено: {error}", file=sys.stderr)
            return 1

        if "--plan" in args:
            if "--json" in args:
                print(plan_json(prepared))
                return 0
            print(f"Пробы: найдено {len(prepared.hits)}, не найдено {len(prepared.misses)}.")
            for index, question in enumerate(prepared.batch, start=1):
                print(f"{index}. {question.text}")
            return 0

        answers_path = _option(args, "--answers")
        if not answers_path:
            print("--report требует --answers <путь>", file=sys.stderr)
            return 2
        try:
            answers = load_answers(Path(answers_path))
        except (OSError, TomlError, InterviewError) as error:
            print(f"ответы не проходят проверку: {error}", file=sys.stderr)
            return 1
        try:
            result = finish(prepared, answers)
        except InterviewError as error:
            print(f"ответы не применены: {error}", file=sys.stderr)
            return 1
        print(report(result))
        return 0

    print(
        "Использование: python -m ai_kit.interview --check [--adapter <путь>] | "
        "--plan [--project <путь>] --profile <путь> --adapter <путь> [--json] | "
        "--report [--project <путь>] --profile <путь> --adapter <путь> --answers <путь>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
