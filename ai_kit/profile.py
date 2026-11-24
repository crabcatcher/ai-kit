"""Профиль проекта: схема как код, провенанс фактов и валидация."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any

from ai_kit.args import UsageError, ensure_known, normalize
from ai_kit.console import configure_console
from ai_kit.rules import LABELS, REQUIRED, Rule, build_rules, rule_errors
from ai_kit.toml_io import TomlError, load as load_toml

PROVENANCE = ("probe", "answer")
VERSION_REQUIRES_PROBE = "версия заполняется только из probe с доказательством"


class ProfileError(Exception):
    def __init__(self, errors: list[tuple[str, str]]) -> None:
        self.errors = errors
        super().__init__("\n".join(f"{path}: {message}" for path, message in errors))


@dataclass(frozen=True)
class Evidence:
    name: str
    version: str | None = None
    evidence: str | None = None
    provenance: str = "probe"


@dataclass(frozen=True)
class Project:
    name: str
    purpose: str
    boundaries: list[str]


@dataclass(frozen=True)
class Docs:
    language: str
    dir: str


@dataclass(frozen=True)
class Stack:
    runtime: list[Evidence]
    frameworks: list[Evidence]
    stores: list[Evidence]
    tools: list[Evidence]


@dataclass(frozen=True)
class Layer:
    name: str
    responsibility: str
    may_depend_on: list[str]


@dataclass(frozen=True)
class Layout:
    source_roots: list[str]
    layers: list[Layer]


@dataclass(frozen=True)
class Tests:
    framework: str | None
    commands: dict[str, str]


@dataclass(frozen=True)
class Process:
    branches: str | None
    review_roles: list[str]
    tiers: bool


@dataclass(frozen=True)
class Automation:
    clients: list[str]
    ci: str | None
    pre_commit: bool


@dataclass(frozen=True)
class Security:
    access_levels: list[str]
    secrets_policy: str | None


@dataclass(frozen=True)
class DisabledGate:
    id: str
    reason: str
    owner: str
    trigger: str


@dataclass(frozen=True)
class Unknown:
    field: str
    question: str


@dataclass(frozen=True)
class Profile:
    schema_version: str
    project: Project
    docs: Docs
    stack: Stack
    layout: Layout
    tests: Tests
    process: Process
    automation: Automation
    security: Security
    rules: list[Rule]
    gates: list[DisabledGate]
    unknowns: list[Unknown]
    adapter_id: str | None


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _check_keys(
    data: dict[str, Any], allowed: set[str], path: str, errors: list[tuple[str, str]]
) -> None:
    prefix = f"{path}." if path else ""
    for key in data:
        if key not in allowed:
            errors.append((f"{prefix}{key}", "неизвестное поле"))


def _strings(value: Any, path: str, errors: list[tuple[str, str]], *, required: bool) -> None:
    if value is None and not required:
        return
    if not isinstance(value, list) or not all(_is_str(item) for item in value):
        errors.append((path, "ожидается список непустых строк"))
        return
    if required and not value:
        errors.append((path, "список не может быть пустым"))


def _text(value: Any, path: str, errors: list[tuple[str, str]], *, required: bool) -> None:
    if value is None and not required:
        return
    if not _is_str(value):
        errors.append((path, "ожидается непустая строка"))


def _flag(value: Any, path: str, errors: list[tuple[str, str]]) -> None:
    if not isinstance(value, bool):
        errors.append((path, "ожидается true или false"))


def _has_value(data: dict[str, Any], path: str) -> bool:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if node is None:
        return False
    if isinstance(node, (str, list, dict)) and not node:
        return False
    return True


def _evidence_list(
    value: Any, path: str, errors: list[tuple[str, str]]
) -> None:
    if not isinstance(value, list):
        errors.append((path, "ожидается список"))
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            errors.append((item_path, "ожидается объект"))
            continue
        _check_keys(item, {"name", "version", "evidence", "provenance"}, item_path, errors)
        _text(item.get("name"), f"{item_path}.name", errors, required=True)
        provenance = item.get("provenance", "probe")
        if provenance not in PROVENANCE:
            errors.append(
                (f"{item_path}.provenance", "ожидается probe или answer (unknown — это отсутствие факта)")
            )
        version = item.get("version")
        if version is not None:
            _text(version, f"{item_path}.version", errors, required=True)
            if provenance != "probe":
                errors.append((f"{item_path}.version", VERSION_REQUIRES_PROBE))
            _text(item.get("evidence"), f"{item_path}.evidence", errors, required=True)


def validate(data: Any) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        return [("", "профиль должен быть объектом")]

    top_allowed = {
        "schema_version", "project", "docs", "stack", "layout", "tests",
        "process", "automation", "security", "rules", "gates", "unknowns",
        "not_applicable", "adapter",
    }
    _check_keys(data, top_allowed, "", errors)
    _text(data.get("schema_version"), "schema_version", errors, required=True)

    def section(key: str) -> dict[str, Any] | None:
        value = data.get(key)
        if not isinstance(value, dict):
            errors.append((key, "ожидается объект"))
            return None
        return value

    project = section("project")
    if project is not None:
        _check_keys(project, {"name", "purpose", "boundaries"}, "project", errors)
        _text(project.get("name"), "project.name", errors, required=True)
        _text(project.get("purpose"), "project.purpose", errors, required=True)
        _strings(project.get("boundaries"), "project.boundaries", errors, required=True)

    docs = section("docs")
    if docs is not None:
        _check_keys(docs, {"language", "dir"}, "docs", errors)
        _text(docs.get("language"), "docs.language", errors, required=True)
        _text(docs.get("dir"), "docs.dir", errors, required=True)

    stack = section("stack")
    if stack is not None:
        _check_keys(stack, {"runtime", "frameworks", "stores", "tools"}, "stack", errors)
        for key in ("runtime", "frameworks", "stores", "tools"):
            _evidence_list(stack.get(key, []), f"stack.{key}", errors)

    layout = section("layout")
    if layout is not None:
        _check_keys(layout, {"source_roots", "layers"}, "layout", errors)
        _strings(layout.get("source_roots"), "layout.source_roots", errors, required=True)
        layers = layout.get("layers", [])
        if not isinstance(layers, list):
            errors.append(("layout.layers", "ожидается список"))
        else:
            seen: set[str] = set()
            for index, layer in enumerate(layers):
                layer_path = f"layout.layers[{index}]"
                if not isinstance(layer, dict):
                    errors.append((layer_path, "ожидается объект"))
                    continue
                _check_keys(layer, {"name", "responsibility", "may_depend_on"}, layer_path, errors)
                _text(layer.get("name"), f"{layer_path}.name", errors, required=True)
                _text(layer.get("responsibility"), f"{layer_path}.responsibility", errors, required=True)
                _strings(layer.get("may_depend_on", []), f"{layer_path}.may_depend_on", errors, required=False)
                name = layer.get("name")
                if _is_str(name):
                    if name in seen:
                        errors.append((f"{layer_path}.name", "имя слоя повторяется"))
                    seen.add(name)

    tests = section("tests")
    if tests is not None:
        _check_keys(tests, {"framework", "commands"}, "tests", errors)
        _text(tests.get("framework"), "tests.framework", errors, required=False)
        commands = tests.get("commands", {})
        if not isinstance(commands, dict) or not all(
            _is_str(role) and _is_str(command) for role, command in commands.items()
        ):
            errors.append(("tests.commands", "ожидается объект «роль → команда»"))

    process = section("process")
    if process is not None:
        _check_keys(process, {"branches", "review_roles", "tiers"}, "process", errors)
        _text(process.get("branches"), "process.branches", errors, required=False)
        _strings(process.get("review_roles"), "process.review_roles", errors, required=True)
        _flag(process.get("tiers"), "process.tiers", errors)

    automation = section("automation")
    if automation is not None:
        _check_keys(automation, {"clients", "ci", "pre_commit"}, "automation", errors)
        _strings(automation.get("clients"), "automation.clients", errors, required=False)
        _text(automation.get("ci"), "automation.ci", errors, required=False)
        _flag(automation.get("pre_commit"), "automation.pre_commit", errors)

    security = section("security")
    if security is not None:
        _check_keys(security, {"access_levels", "secrets_policy"}, "security", errors)
        _strings(security.get("access_levels"), "security.access_levels", errors, required=True)
        _text(security.get("secrets_policy"), "security.secrets_policy", errors, required=False)

    errors.extend(rule_errors(data.get("rules", []), "P", "rules"))

    gates = data.get("gates", {})
    if not isinstance(gates, dict):
        errors.append(("gates", "ожидается объект"))
    else:
        _check_keys(gates, {"disabled"}, "gates", errors)
        disabled = gates.get("disabled", [])
        if not isinstance(disabled, list):
            errors.append(("gates.disabled", "ожидается список"))
        else:
            for index, gate in enumerate(disabled):
                gate_path = f"gates.disabled[{index}]"
                if not isinstance(gate, dict):
                    errors.append((gate_path, "ожидается объект"))
                    continue
                _check_keys(gate, {"id", "reason", "owner", "trigger"}, gate_path, errors)
                for key in ("id", "reason", "owner", "trigger"):
                    _text(gate.get(key), f"{gate_path}.{key}", errors, required=True)

    unknowns = data.get("unknowns", [])
    if not isinstance(unknowns, list):
        errors.append(("unknowns", "ожидается список"))
    else:
        for index, unknown in enumerate(unknowns):
            unknown_path = f"unknowns[{index}]"
            if not isinstance(unknown, dict):
                errors.append((unknown_path, "ожидается объект"))
                continue
            _check_keys(unknown, {"field", "question"}, unknown_path, errors)
            _text(unknown.get("field"), f"{unknown_path}.field", errors, required=True)
            _text(unknown.get("question"), f"{unknown_path}.question", errors, required=True)

    not_applicable = data.get("not_applicable", [])
    if not isinstance(not_applicable, list):
        errors.append(("not_applicable", "ожидается список"))
    else:
        from ai_kit import schema as profile_schema

        seen_fields: set[str] = set()
        for index, entry in enumerate(not_applicable):
            place = f"not_applicable[{index}]"
            if not isinstance(entry, dict):
                errors.append((place, "ожидается объект"))
                continue
            _check_keys(entry, {"field", "reason"}, place, errors)
            field = entry.get("field")
            _text(field, f"{place}.field", errors, required=True)
            _text(entry.get("reason"), f"{place}.reason", errors, required=True)
            if not _is_str(field):
                continue
            if profile_schema.resolve(field) is None:
                errors.append((f"{place}.field", "поля нет в схеме профиля"))
                continue
            if profile_schema.is_required(field):
                errors.append((f"{place}.field", "обязательное поле нельзя объявить неприменимым"))
            if field in seen_fields:
                errors.append((f"{place}.field", "поле указано дважды"))
            seen_fields.add(field)
            if _has_value(data, field):
                errors.append((f"{place}.field", "поле заполнено и объявлено неприменимым"))

    adapter = data.get("adapter", {})
    if not isinstance(adapter, dict):
        errors.append(("adapter", "ожидается объект"))
    else:
        _check_keys(adapter, {"id"}, "adapter", errors)
        _text(adapter.get("id"), "adapter.id", errors, required=False)

    return errors


def parse(data: Any) -> Profile:
    errors = validate(data)
    if errors:
        raise ProfileError(errors)

    evidence = lambda items: [Evidence(**item) for item in items]  # noqa: E731
    return Profile(
        schema_version=data["schema_version"],
        project=Project(**data["project"]),
        docs=Docs(**data["docs"]),
        stack=Stack(**{key: evidence(data["stack"].get(key, [])) for key in ("runtime", "frameworks", "stores", "tools")}),
        layout=Layout(
            source_roots=data["layout"]["source_roots"],
            layers=[Layer(**layer) for layer in data["layout"].get("layers", [])],
        ),
        tests=Tests(
            framework=data["tests"].get("framework"),
            commands=data["tests"].get("commands", {}),
        ),
        process=Process(
            branches=data["process"].get("branches"),
            review_roles=data["process"]["review_roles"],
            tiers=data["process"]["tiers"],
        ),
        automation=Automation(
            clients=data["automation"].get("clients", []),
            ci=data["automation"].get("ci"),
            pre_commit=data["automation"]["pre_commit"],
        ),
        security=Security(
            access_levels=data["security"]["access_levels"],
            secrets_policy=data["security"].get("secrets_policy"),
        ),
        rules=build_rules(data.get("rules", [])),
        gates=[DisabledGate(**gate) for gate in data.get("gates", {}).get("disabled", [])],
        unknowns=[Unknown(**unknown) for unknown in data.get("unknowns", [])],
        adapter_id=data.get("adapter", {}).get("id"),
    )


def load(path: Path) -> Profile:
    return parse(load_toml(path))


def _evidence_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["name", "provenance"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "version": {"type": "string", "minLength": 1},
                "evidence": {"type": "string", "minLength": 1},
                "provenance": {"enum": list(PROVENANCE)},
            },
            "allOf": [
                {
                    "if": {"required": ["version"]},
                    "then": {
                        "required": ["provenance", "evidence"],
                        "properties": {"provenance": {"const": "probe"}},
                    },
                }
            ],
        },
    }


def _rule_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "required": list(REQUIRED),
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "text": {"type": "string", "minLength": 1},
                "label": {"enum": list(LABELS)},
                "executor": {"type": "string", "minLength": 1},
                "catcher": {"type": "string", "minLength": 1},
                "owner": {"type": "string", "minLength": 1},
                "trigger": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            "allOf": [
                {
                    "if": {"properties": {"label": {"const": "нет гейта"}}, "required": ["label"]},
                    "then": {"required": ["reason"]},
                }
            ],
        },
    }


def print_schema() -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Профиль проекта",
        "type": "object",
        "required": ["schema_version", "project", "docs", "stack", "layout", "tests", "process", "automation", "security"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "minLength": 1},
            "project": {
                "type": "object",
                "required": ["name", "purpose", "boundaries"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "purpose": {"type": "string", "minLength": 1},
                    "boundaries": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                },
            },
            "docs": {
                "type": "object",
                "required": ["language", "dir"],
                "additionalProperties": False,
                "properties": {"language": {"type": "string", "minLength": 1}, "dir": {"type": "string", "minLength": 1}},
            },
            "stack": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "runtime": _evidence_schema(),
                    "frameworks": _evidence_schema(),
                    "stores": _evidence_schema(),
                    "tools": _evidence_schema(),
                },
            },
            "layout": {
                "type": "object",
                "required": ["source_roots"],
                "additionalProperties": False,
                "properties": {
                    "source_roots": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "layers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "responsibility"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "responsibility": {"type": "string"},
                                "may_depend_on": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
            "tests": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "framework": {"type": "string"},
                    "commands": {"type": "object", "additionalProperties": {"type": "string"}},
                },
            },
            "process": {
                "type": "object",
                "required": ["review_roles", "tiers"],
                "additionalProperties": False,
                "properties": {
                    "branches": {"type": "string"},
                    "review_roles": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "tiers": {"type": "boolean"},
                },
            },
            "automation": {
                "type": "object",
                "required": ["pre_commit"],
                "additionalProperties": False,
                "properties": {
                    "clients": {"type": "array", "items": {"type": "string"}},
                    "ci": {"type": "string"},
                    "pre_commit": {"type": "boolean"},
                },
            },
            "security": {
                "type": "object",
                "required": ["access_levels"],
                "additionalProperties": False,
                "properties": {
                    "access_levels": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "secrets_policy": {"type": "string"},
                },
            },
            "rules": _rule_schema(),
            "gates": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "disabled": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "reason", "owner", "trigger"],
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string"},
                                "reason": {"type": "string"},
                                "owner": {"type": "string"},
                                "trigger": {"type": "string"},
                            },
                        },
                    }
                },
            },
            "unknowns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["field", "question"],
                    "additionalProperties": False,
                    "properties": {
                        "field": {"type": "string"},
                        "question": {"type": "string"},
                    },
                },
            },
            "not_applicable": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["field", "reason"],
                    "additionalProperties": False,
                    "properties": {
                        "field": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "adapter": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"type": "string"}},
            },
        },
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = normalize(sys.argv[1:] if argv is None else argv)
    try:
        ensure_known(args, {"--check", "--print-schema", "--help"})
    except UsageError as error:
        print(f"{error}\nИспользование: python -m ai_kit.profile --help", file=sys.stderr)
        return 2
    if "--help" in args:
        print(__doc__)
        return 0
    if "--check" in args and "--print-schema" in args:
        print("--check и --print-schema несовместимы", file=sys.stderr)
        return 2
    if "--check" in args:
        index = args.index("--check")
        if index + 1 >= len(args) or args[index + 1].startswith("-"):
            print("--check требует путь к профилю", file=sys.stderr)
            return 2
        try:
            load(Path(args[index + 1]))
        except (OSError, TomlError) as error:
            print(f"не удалось прочитать профиль: {error}", file=sys.stderr)
            return 1
        except ProfileError as error:
            print(error, file=sys.stderr)
            return 1
        print("Профиль валиден.")
        return 0
    if "--print-schema" in args:
        print(print_schema())
        return 0
    print("Использование: python -m ai_kit.profile --print-schema | --check <путь>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
