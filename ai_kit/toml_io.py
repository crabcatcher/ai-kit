"""Чтение TOML: UTF-8 с BOM, имя файла в сообщении об ошибке."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class TomlError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def load(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise TomlError(f"{path}: файл не читается: {error}") from error
    return loads(text, str(path))


def loads(text: str, source: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise TomlError(f"{source}: {error}") from error
