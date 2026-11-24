"""Диагностика проверок: одна форма для человека, машины и корпуса."""

from __future__ import annotations

import json
from dataclasses import dataclass

SEVERITIES = ("error", "warning", "gap")
SEVERITY_ORDER = {"error": 0, "warning": 1, "gap": 2}
SEVERITY_TITLES = {"error": "ошибка", "warning": "предупреждение", "gap": "пробел"}


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    path: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"неизвестный уровень: {self.severity}")


@dataclass(frozen=True)
class Report:
    diagnostics: tuple[Diagnostic, ...]

    def sorted(self) -> tuple[Diagnostic, ...]:
        return tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (SEVERITY_ORDER[item.severity], item.code, item.path, item.message),
            )
        )

    def count(self, severity: str) -> int:
        return sum(1 for item in self.diagnostics if item.severity == severity)

    @property
    def errors(self) -> int:
        return self.count("error")

    @property
    def warnings(self) -> int:
        return self.count("warning")

    @property
    def gaps(self) -> int:
        return self.count("gap")

    @property
    def ok(self) -> bool:
        return self.errors == 0

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_table(self) -> str:
        if not self.diagnostics:
            return "Нарушений нет."
        lines = ["| Уровень | Код | Место | Сообщение |", "|---|---|---|---|"]
        for item in self.sorted():
            lines.append(
                f"| {SEVERITY_TITLES[item.severity]} | {_cell(item.code)} | "
                f"{_cell(item.path)} | {_cell(item.message)} |"
            )
        return "\n".join(lines)

    def summary(self) -> str:
        return f"Ошибок: {self.errors}, предупреждений: {self.warnings}, пробелов: {self.gaps}."

    def to_json(self) -> str:
        data = {
            "summary": {"errors": self.errors, "warnings": self.warnings, "gaps": self.gaps},
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "path": item.path,
                    "message": item.message,
                }
                for item in self.sorted()
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
