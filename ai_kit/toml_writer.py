"""Запись TOML средствами стандартной библиотеки: без внешних зависимостей."""

from __future__ import annotations

import json
import re
from typing import Any

BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class TomlWriteError(Exception):
    pass


def dumps(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        raise TomlWriteError("корень профиля должен быть объектом")
    lines: list[str] = []
    _emit_table(lines, [], data)
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _emit_table(lines: list[str], prefix: list[str], table: dict[str, Any]) -> None:
    for key, value in table.items():
        if not _is_tableish(value):
            lines.append(f"{_key(key)} = {_inline(value, '.'.join(prefix + [key]))}")

    for key, value in table.items():
        if isinstance(value, dict):
            _blank(lines)
            lines.append(f"[{'.'.join(prefix + [key])}]")
            _emit_table(lines, prefix + [key], value)
        elif _is_table_array(value):
            for item in value:
                _blank(lines)
                lines.append(f"[[{'.'.join(prefix + [key])}]]")
                _emit_table(lines, prefix + [key], item)


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _is_tableish(value: Any) -> bool:
    return isinstance(value, dict) or _is_table_array(value)


def _blank(lines: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")


def _key(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise TomlWriteError("ключ должен быть непустой строкой")
    return name if BARE_KEY.fullmatch(name) else json.dumps(name, ensure_ascii=False)


def _inline(value: Any, path: str) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        if _is_table_array(value):
            raise TomlWriteError(f"{path}: список объектов пишется таблицами, а не строкой")
        return "[" + ", ".join(_inline(item, path) for item in value) + "]"
    if isinstance(value, dict):
        raise TomlWriteError(f"{path}: объект пишется таблицей, а не строкой")
    raise TomlWriteError(f"{path}: значение типа {type(value).__name__} не сериализуется")
