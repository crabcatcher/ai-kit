"""Артефакты проверки перед коммитом: переносимый скрипт и git-хук."""

from __future__ import annotations

import os
import shlex
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
    if not roles and commands.get("build"):
        roles = ["build"]
    return [(role, str(commands[role])) for role in roles]


def split_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=os.name != "nt")
    return parts or [command]


def files(data: dict[str, Any]) -> dict[str, str]:
    selected = [(role, split_command(command)) for role, command in select_commands(data)]
    if not selected:
        return {}
    return {PRE_COMMIT: script(selected), HOOK: hook()}


def script(commands: list[tuple[str, list[str]]]) -> str:
    return (
        '#!/usr/bin/env python3\n'
        '"""Проверки перед коммитом: остановиться на первой ошибке."""\n'
        "\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        f"COMMANDS = {commands!r}\n"
        "\n"
        "for stream in (sys.stdout, sys.stderr):\n"
        "    reconfigure = getattr(stream, \"reconfigure\", None)\n"
        "    if reconfigure is not None:\n"
        "        reconfigure(encoding=\"utf-8\")\n"
        "\n"
        "\n"
        "def argv(command: list[str]) -> list[str]:\n"
        '    if os.name == "nt" and command[0].lower().endswith((".bat", ".cmd")):\n'
        '        return ["cmd", "/c", *command]\n'
        "    return command\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    for role, command in COMMANDS:\n"
        '        print(f"[{role}] {command}", flush=True)\n'
        "        result = subprocess.run(argv(command))\n"
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
    return (
        "#!/bin/sh\n"
        'configured=$(git config --get hooks.interpreter 2>/dev/null || true)\n'
        'if [ -n "$configured" ]; then exec "$configured" tools/pre-commit.py; fi\n'
        'if [ -n "$PRECOMMIT_PYTHON" ]; then exec "$PRECOMMIT_PYTHON" tools/pre-commit.py; fi\n'
        "for candidate in python3 python py; do\n"
        '  if command -v "$candidate" >/dev/null 2>&1; then\n'
        '    if [ "$candidate" = "py" ]; then\n'
        '      py -3 -c "import sys" >/dev/null 2>&1 && exec py -3 tools/pre-commit.py\n'
        "    else\n"
        '      "$candidate" -c "import sys" >/dev/null 2>&1 && exec "$candidate" tools/pre-commit.py\n'
        "    fi\n"
        "  fi\n"
        "done\n"
        'echo "Не найден рабочий Python: задайте git config hooks.interpreter <путь> или PRECOMMIT_PYTHON" >&2\n'
        "exit 1\n"
    )
