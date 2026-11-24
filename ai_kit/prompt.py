"""Печать готового промпта для агента: путь к стартеру подставляется сам."""

from __future__ import annotations

import sys
from pathlib import Path

from ai_kit.console import configure_console


def launcher(kit_root: Path) -> str:
    root = kit_root.resolve()
    return (
        f"Стартер: {root}\n"
        "Проект: текущий каталог.\n"
        f"Прочитай {root / 'docs' / 'agent-prompt.md'} и выполни его целиком.\n"
    )


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = sys.argv[1:] if argv is None else argv
    if "--help" in args:
        print(__doc__)
        return 0
    print(launcher(Path(__file__).resolve().parents[1]), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
