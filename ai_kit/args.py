"""Строгая проверка параметров CLI: неизвестный флаг — ошибка, не тишина."""

from __future__ import annotations


class UsageError(Exception):
    pass


def normalize(args: list[str]) -> list[str]:
    result: list[str] = []
    for arg in args:
        if arg == "-h":
            result.append("--help")
        elif arg.startswith("--") and "=" in arg:
            name, value = arg.split("=", 1)
            result.extend((name, value))
        else:
            result.append(arg)
    return result


def ensure_known(args: list[str], allowed: set[str]) -> None:
    for arg in args:
        if arg.startswith("-") and arg not in allowed:
            raise UsageError(f"неизвестный параметр: {arg}")
