"""Публичные входы проекта: форма, канонизация пути и перенос в профиль."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
FEATURE = re.compile(r"^features/[A-Za-z0-9_-][A-Za-z0-9._-]*\.md$")
FIELDS = {"id", "kind", "locator", "feature"}


class InputError(Exception):
    def __init__(self, errors: list[tuple[str, str]]) -> None:
        self.errors = errors
        super().__init__("\n".join(f"{path}: {message}" for path, message in errors))


@dataclass(frozen=True)
class Input:
    id: str
    kind: str
    locator: str
    feature: str | None = None


def canonical_feature(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return None
    name = value.strip()
    if name.startswith("features/"):
        name = name[len("features/"):]
    if "/" in name or ".." in name or name.startswith("."):
        return None
    if FEATURE.fullmatch(f"features/{name}") is None:
        return None
    return f"features/{name}"


def errors(entries: Any, place: str = "inputs") -> list[tuple[str, str]]:
    if not isinstance(entries, list):
        return [(place, "ожидается список")]
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        path = f"{place}[{index}]"
        if not isinstance(entry, dict):
            result.append((path, "ожидается объект"))
            continue
        for key in sorted(set(entry) - FIELDS):
            result.append((f"{path}.{key}", "неизвестное поле"))
        value = entry.get("id")
        if not isinstance(value, str) or ID.fullmatch(value) is None:
            result.append((f"{path}.id", "ожидается slug: строчные латинские буквы, цифры, точка, дефис, подчёркивание"))
        elif value in seen:
            result.append((f"{path}.id", "id повторяется"))
        else:
            seen.add(value)
        if not isinstance(entry.get("kind"), str) or not entry["kind"].strip():
            result.append((f"{path}.kind", "ожидается непустая строка"))
        if not isinstance(entry.get("locator"), str) or not entry["locator"].strip():
            result.append((f"{path}.locator", "ожидается непустая строка"))
        if "feature" in entry and canonical_feature(entry["feature"]) is None:
            result.append((f"{path}.feature", "ожидается features/<имя>.md внутри docs.dir"))
    return result


def parse(entries: Any) -> list[Input]:
    problems = errors(entries)
    if problems:
        raise InputError(problems)
    return [
        Input(
            id=entry["id"],
            kind=entry["kind"],
            locator=entry["locator"],
            feature=canonical_feature(entry.get("feature")) if "feature" in entry else None,
        )
        for entry in entries
    ]


def as_dict(entry: Input) -> dict[str, str]:
    data = {"id": entry.id, "kind": entry.kind, "locator": entry.locator}
    if entry.feature:
        data["feature"] = entry.feature
    return data


def apply(values: dict[str, Any], adapter: dict[str, Any]) -> dict[str, Any]:
    if "inputs" not in adapter:
        return values
    incoming = parse(adapter["inputs"])
    result = copy.deepcopy(values)
    if "inputs" not in result:
        result["inputs"] = [as_dict(entry) for entry in incoming]
        return result
    current = parse(result["inputs"])
    if sorted(current, key=lambda item: item.id) == sorted(incoming, key=lambda item: item.id):
        return result
    raise InputError(
        [
            (
                "inputs",
                "профиль и адаптер расходятся: профиль — источник истины; "
                "обновите профиль или уберите inputs из адаптера",
            )
        ]
    )
