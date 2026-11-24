"""Единая точка входа: ai-kit <команда> ..."""

from __future__ import annotations

import sys

from ai_kit import doctor, init, interview, profile, prompt, templates
from ai_kit.console import configure_console

COMMANDS = {
    "doctor": doctor.main,
    "init": init.main,
    "interview": interview.main,
    "profile": profile.main,
    "prompt": prompt.main,
    "templates": templates.main,
}

USAGE = (
    "ai-kit <команда> [параметры]\n"
    "Команды: doctor, init, interview, profile, prompt, templates.\n"
    "Параметры команды: ai-kit <команда> --help"
)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    command = args[0]
    handler = COMMANDS.get(command)
    if handler is None:
        print(f"неизвестная команда: {command}\n{USAGE}", file=sys.stderr)
        return 2
    return handler(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
