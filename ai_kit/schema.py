"""Схема профиля как данные: разбор путей и распознавание полей-доказательств."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from ai_kit.profile import print_schema


@lru_cache(maxsize=1)
def profile_schema() -> dict[str, Any]:
    return json.loads(print_schema())


def resolve(path: str, node: dict[str, Any] | None = None) -> dict[str, Any] | None:
    current = profile_schema() if node is None else node
    for part in path.split("."):
        properties = current.get("properties")
        if properties is None or part not in properties:
            return None
        current = properties[part]
    return current


def is_evidence(node: dict[str, Any] | None) -> bool:
    if node is None or node.get("type") != "array":
        return False
    properties = node.get("items", {}).get("properties", {})
    return "name" in properties and "provenance" in properties
