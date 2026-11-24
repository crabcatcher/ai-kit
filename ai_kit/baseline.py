"""Машинный baseline: принятые нарушения правил с владельцем и триггером."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIELDS = ("rule", "path", "reason", "owner", "trigger")
NO_VALUE = "—"
RULE = re.compile(r"^[KAP]\d+$")
DRIVE = re.compile(r"^[A-Za-z]:")
TEMPLATE = 'schema_version = "1"\n\n# Принятые нарушения: rule, path, reason, owner, trigger.\n'


class BaselineError(Exception):
    def __init__(self, errors: list[tuple[str, str]]) -> None:
        self.errors = errors
        super().__init__("\n".join(f"{path}: {message}" for path, message in errors))


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    reason: str
    owner: str
    trigger: str


def load(path: Path, rule_ids: set[str] | None = None) -> list[Violation]:
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return parse(data, rule_ids)


def parse(data: Any, rule_ids: set[str] | None = None) -> list[Violation]:
    errors = violation_errors(data, rule_ids)
    if errors:
        raise BaselineError(errors)
    entries = data.get("violation", [])
    return [Violation(**entry) for entry in entries]


def violation_errors(data: Any, rule_ids: set[str] | None = None) -> list[tuple[str, str]]:
    if not isinstance(data, dict):
        return [("", "baseline должен быть объектом")]
    errors: list[tuple[str, str]] = []
    for key in sorted(set(data) - {"schema_version", "violation"}):
        errors.append((key, "неизвестное поле"))
    if not isinstance(data.get("schema_version"), str) or not data["schema_version"].strip():
        errors.append(("schema_version", "ожидается непустая строка"))

    entries = data.get("violation", [])
    if not isinstance(entries, list):
        return errors + [("violation", "ожидается список")]

    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        place = f"violation[{index}]"
        if not isinstance(entry, dict):
            errors.append((place, "ожидается объект"))
            continue
        for key in sorted(set(entry) - set(FIELDS)):
            errors.append((f"{place}.{key}", "неизвестное поле"))
        for key in FIELDS:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append((f"{place}.{key}", "ожидается непустая строка"))

        rule = entry.get("rule")
        if isinstance(rule, str):
            if RULE.fullmatch(rule) is None:
                errors.append((f"{place}.rule", "номер правила: K, A или P и цифры"))
            elif rule_ids is not None and rule not in rule_ids:
                errors.append((f"{place}.rule", f"правила {rule} нет в реестре"))

        path = entry.get("path")
        if isinstance(path, str) and path:
            if path.startswith(("/", "\\")) or DRIVE.match(path):
                errors.append((f"{place}.path", "путь должен быть относительным"))
            elif ".." in path.split("/"):
                errors.append((f"{place}.path", "путь не может выходить вверх"))

        if entry.get("trigger") == NO_VALUE:
            errors.append((f"{place}.trigger", "нужен наблюдаемый триггер"))

        key = (str(entry.get("rule")), str(entry.get("path")))
        if key in seen:
            errors.append((place, "правило и место повторяются"))
        seen.add(key)
    return errors
