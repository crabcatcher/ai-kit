"""Артефакты проверки перед коммитом: переносимый скрипт и git-хук."""

from __future__ import annotations

from typing import Any

PRE_COMMIT = "tools/pre-commit.py"
HOOK = "tools/hooks/pre-commit"


def select_commands(data: dict[str, Any]) -> list[tuple[str, str]]:
    commands = data.get("tests", {}).get("commands", {})
    if not isinstance(commands, dict):
        return []
    roles = [role for role in ("unit", "lint") if commands.get(role)]
    if not roles and commands.get("test"):
        roles = ["test"]
    return [(role, str(commands[role])) for role in roles]


def files(data: dict[str, Any]) -> dict[str, str]:
    commands = select_commands(data)
    if not commands:
        return {}
    return {PRE_COMMIT: script(commands), HOOK: hook()}


def script(commands: list[tuple[str, str]]) -> str:
    return (
        '#!/usr/bin/env python3\n'
        '"""Проверки перед коммитом: остановиться на первой ошибке."""\n'
        "\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        f"COMMANDS = {commands!r}\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    for role, command in COMMANDS:\n"
        '        print(f"[{role}] {command}", flush=True)\n'
        "        result = subprocess.run(command, shell=True)\n"
        "        if result.returncode != 0:\n"
        '            print(f"проверка {role} не прошла", file=sys.stderr)\n'
        "            return 1\n"
        "    return 0\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )


def hook() -> str:
    return '#!/bin/sh\npython tools/pre-commit.py\n'
