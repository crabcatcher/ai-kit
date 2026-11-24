"""Строгая проверка параметров CLI: неизвестный флаг — ошибка, не тишина."""

from __future__ import annotations


class UsageError(Exception):
    pass


def ensure_known(args: list[str], allowed: set[str]) -> None:
    for arg in args:
        if not arg.startswith("--"):
            continue
        name = arg.split("=", 1)[0]
        if name not in allowed:
            raise UsageError(f"неизвестный параметр: {name}")
