"""Detect адаптера: маски, по которым он узнаёт проект."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load(adapter: dict[str, Any]) -> tuple[list[str], list[tuple[str, str]]]:
    entries = adapter.get("detect", [])
    if not isinstance(entries, list):
        return [], [("detect", "ожидается список масок")]
    patterns: list[str] = []
    errors: list[tuple[str, str]] = []
    for index, entry in enumerate(entries):
        place = f"detect[{index}]"
        if isinstance(entry, str):
            pattern = entry
        elif isinstance(entry, dict):
            extra = set(entry) - {"glob"}
            if extra:
                errors.append((place, f"неизвестные поля: {', '.join(sorted(extra))}"))
            pattern = entry.get("glob")
        else:
            errors.append((place, "ожидается строка или объект с glob"))
            continue
        if not isinstance(pattern, str) or not pattern.strip():
            errors.append((place, "ожидается непустая маска"))
            continue
        if pattern.startswith(("/", "\\")) or pattern[1:3] == ":\\" or pattern[1:3] == ":/":
            errors.append((place, "маска должна быть относительной"))
            continue
        if ".." in pattern.split("/") or "\\" in pattern:
            errors.append((place, "маска без .. и обратного слэша"))
            continue
        patterns.append(pattern)
    return patterns, errors


def matches(root: Path, patterns: list[str]) -> bool:
    for pattern in patterns:
        try:
            if next(iter(root.glob(pattern)), None) is not None:
                return True
        except (OSError, ValueError, NotImplementedError):
            continue
    return False
