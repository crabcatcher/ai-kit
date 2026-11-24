"""Расположение ресурсов набора и понятная ошибка для неустановленной копии."""

from __future__ import annotations

from pathlib import Path


class KitRootError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def kit_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if not (root / "documents.toml").is_file():
        raise KitRootError(
            "ресурсы ai-kit не найдены: нужна editable-установка из репозитория "
            "(`python -m pip install -e .`), обычный pip install пакет не содержит шаблоны"
        )
    return root
