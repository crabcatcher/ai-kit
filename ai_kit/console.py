"""Единая настройка вывода: русский текст в консоли Windows ломается без UTF-8."""

from __future__ import annotations

import sys


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
