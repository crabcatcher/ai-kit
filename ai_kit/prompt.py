"""Печать готового промпта для агента: путь к стартеру подставляется сам."""

from __future__ import annotations

import sys
from pathlib import Path

from ai_kit.args import UsageError, ensure_known, normalize
from ai_kit.console import configure_console
from ai_kit.paths import KitRootError, kit_root


def launcher(kit_root: Path) -> str:
    root = kit_root.resolve()
    return (
        f"Стартер: {root}\n"
        "Проект: текущий каталог.\n"
        f"Прочитай {root / 'docs' / 'agent-prompt.md'} и выполни его целиком.\n"
    )


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = normalize(sys.argv[1:] if argv is None else argv)
    try:
        ensure_known(args, {"--help"})
    except UsageError as error:
        print(error, file=sys.stderr)
        return 2
    if "--help" in args:
        print(__doc__)
        return 0
    try:
        root = kit_root()
    except KitRootError as error:
        print(error, file=sys.stderr)
        return 2
    print(launcher(root), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
