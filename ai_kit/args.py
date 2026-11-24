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


def ensure_known(args: list[str], allowed: set[str], value_options: set[str] | None = None) -> None:
    takes_value = value_options or set()
    for index, arg in enumerate(args):
        if index > 0 and args[index - 1] in takes_value:
            if arg in allowed:
                raise UsageError(f"параметру {args[index - 1]} нужно значение")
            if not arg.startswith("--"):
                continue
        if arg in takes_value:
            continue
        if arg.startswith("-") and arg not in allowed:
            raise UsageError(f"неизвестный параметр: {arg}")
