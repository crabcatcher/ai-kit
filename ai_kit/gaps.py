"""Заявленные пробелы адаптера: форма и загрузка."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FIELDS = ("id", "reason", "owner", "trigger")


class GapError(Exception):
    def __init__(self, errors: list[tuple[str, str]]) -> None:
        self.errors = errors
        super().__init__("\n".join(f"{path}: {message}" for path, message in errors))


@dataclass(frozen=True)
class Gap:
    id: str
    reason: str
    owner: str
    trigger: str


def errors(entries: Any, place: str = "gaps") -> list[tuple[str, str]]:
    if not isinstance(entries, list):
        return [(place, "ожидается список")]
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        path = f"{place}[{index}]"
        if not isinstance(entry, dict):
            result.append((path, "ожидается объект"))
            continue
        for key in sorted(set(entry) - set(FIELDS)):
            result.append((f"{path}.{key}", "неизвестное поле"))
        for key in FIELDS:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                result.append((f"{path}.{key}", "ожидается непустая строка"))
        if entry.get("trigger") == "—":
            result.append((f"{path}.trigger", "нужен наблюдаемый триггер"))
        value = entry.get("id")
        if isinstance(value, str) and value.strip():
            if value in seen:
                result.append((f"{path}.id", "идентификатор повторяется"))
            seen.add(value)
    return result


def load(entries: Any) -> list[Gap]:
    problems = errors(entries)
    if problems:
        raise GapError(problems)
    return [Gap(**{key: entry[key] for key in FIELDS}) for entry in entries]


def parse(entries: Any) -> list[Gap]:
    return load(entries)
