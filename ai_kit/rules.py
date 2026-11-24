"""Строки реестра: метки, разбор строк адаптера и профиля, канон набора."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LABELS = ("гейт", "гейт частичный", "измеритель", "дисциплина", "нет гейта", "справочник")
REQUIRED = ("id", "text", "label", "executor", "catcher", "owner", "trigger")
NO_VALUE = "—"


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    label: str
    executor: str
    catcher: str
    owner: str
    trigger: str
    reason: str | None = None


def rule_errors(value: Any, space: str, path: str) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    if not isinstance(value, list):
        return [(path, "ожидается список правил")]

    seen: set[str] = set()
    for index, entry in enumerate(value):
        place = f"{path}[{index}]"
        if not isinstance(entry, dict):
            errors.append((place, "ожидается объект"))
            continue
        for key in sorted(set(entry) - set(REQUIRED) - {"reason"}):
            errors.append((f"{place}.{key}", "неизвестное поле"))
        for key in REQUIRED:
            item = entry.get(key)
            if not isinstance(item, str) or not item.strip():
                errors.append((f"{place}.{key}", "ожидается непустая строка"))

        rule_id = entry.get("id")
        if isinstance(rule_id, str) and re.fullmatch(rf"{space}\d+", rule_id) is None:
            errors.append((f"{place}.id", f"номер начинается с {space} и содержит цифры"))
        if isinstance(rule_id, str):
            if rule_id in seen:
                errors.append((f"{place}.id", "номер повторяется"))
            seen.add(rule_id)

        label = entry.get("label")
        if isinstance(label, str) and label not in LABELS:
            errors.append((f"{place}.label", f"ожидается одно из: {', '.join(LABELS)}"))
        if label in ("гейт", "гейт частичный") and entry.get("executor") == NO_VALUE:
            errors.append((f"{place}.executor", "у гейта есть исполнитель"))
        if label in ("дисциплина", "измеритель") and entry.get("catcher") == NO_VALUE:
            errors.append((f"{place}.catcher", "у правила без гейта есть тот, кто ловит"))
        if label == "нет гейта":
            reason = entry.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append((f"{place}.reason", "у строки без гейта есть причина"))
            if entry.get("trigger") == NO_VALUE:
                errors.append((f"{place}.trigger", "у строки без гейта есть наблюдаемый триггер"))
    return errors


def build_rules(value: Any) -> list[Rule]:
    return [
        Rule(
            id=entry["id"],
            text=entry["text"],
            label=entry["label"],
            executor=entry["executor"],
            catcher=entry["catcher"],
            owner=entry["owner"],
            trigger=entry["trigger"],
            reason=entry.get("reason"),
        )
        for entry in value
    ]


def load_canon(path: Path) -> list[Rule]:
    section: int | None = None
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if "Канон" in stripped:
                section = len(rows)
                continue
            if section is not None:
                break
        if section is None or not stripped.startswith("| K"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 7:
            raise ValueError(f"{path}: строка канона должна иметь 7 колонок: {stripped}")
        rows.append(dict(zip(("id", "text", "label", "executor", "catcher", "owner", "trigger"), cells)))

    errors = rule_errors(rows, "K", "canon")
    if errors:
        raise ValueError("; ".join(f"{place}: {message}" for place, message in errors))
    return build_rules(rows)
