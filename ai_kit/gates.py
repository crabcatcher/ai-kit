"""Внешние гейты: доступность бинаря, диапазон версий, запуск и коды возврата."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_kit.diagnostics import Diagnostic

GATE_TIMEOUT = 120
PROBE_TIMEOUT = 30
OUTPUT_LIMIT = 300
VERSION = re.compile(r"\d+(?:\.\d+){0,3}")
CONDITION = re.compile(r"^(>=|<=|==|>|<)?\s*(\d+(?:\.\d+)*)$")
ALLOWED_GATE_KEYS = {"id", "invariant", "requires", "command", "on_unavailable"}
ALLOWED_REQUIRES_KEYS = {"binary", "version"}


@dataclass(frozen=True)
class Gate:
    id: str
    invariant: str
    binary: str | None
    version: str | None
    argv: tuple[str, ...]


def load_gates(adapter: dict[str, Any]) -> tuple[list[Gate], list[Diagnostic]]:
    entries = adapter.get("gates", [])
    if not isinstance(entries, list):
        return [], [_error("gates", "ожидается список гейтов")]

    gates: list[Gate] = []
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        place = f"gates[{index}]"
        if not isinstance(entry, dict):
            diagnostics.append(_error(place, "ожидается объект"))
            continue
        for key in sorted(set(entry) - ALLOWED_GATE_KEYS):
            diagnostics.append(_error(f"{place}.{key}", "неизвестное поле"))

        gate_id = entry.get("id")
        if not isinstance(gate_id, str) or not gate_id.strip():
            diagnostics.append(_error(f"{place}.id", "ожидается непустая строка"))
            gate_id = ""
        elif gate_id in seen:
            diagnostics.append(_error(f"{place}.id", "идентификатор повторяется"))
        seen.add(gate_id)

        invariant = entry.get("invariant")
        if not isinstance(invariant, str) or not invariant.strip():
            diagnostics.append(_error(f"{place}.invariant", "ожидается непустая строка"))
            invariant = ""

        binary, version, requires_errors = _requires(entry.get("requires", {}), f"{place}.requires")
        diagnostics.extend(requires_errors)

        argv, command_errors = _command(entry.get("command"), f"{place}.command")
        diagnostics.extend(command_errors)

        if entry.get("on_unavailable") != "gap":
            diagnostics.append(_error(f"{place}.on_unavailable", "поддерживается только gap"))

        if gate_id and invariant and argv:
            gates.append(Gate(gate_id, invariant, binary, version, argv))
    return gates, diagnostics


def _requires(value: Any, path: str) -> tuple[str | None, str | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    if not isinstance(value, dict):
        return None, None, [_error(path, "ожидается объект")]
    for key in sorted(set(value) - ALLOWED_REQUIRES_KEYS):
        diagnostics.append(_error(f"{path}.{key}", "неизвестное поле"))
    binary = value.get("binary")
    if binary is not None and (not isinstance(binary, str) or not binary.strip()):
        diagnostics.append(_error(f"{path}.binary", "ожидается непустая строка"))
        binary = None
    version = value.get("version")
    if version is not None:
        if not isinstance(version, str) or not version.strip():
            diagnostics.append(_error(f"{path}.version", "ожидается непустая строка"))
            version = None
        else:
            try:
                satisfies("0.0.0", version)
            except ValueError as error:
                diagnostics.append(_error(f"{path}.version", f"диапазон не разбирается: {error}"))
    return binary, version, diagnostics


def _command(value: Any, path: str) -> tuple[tuple[str, ...], list[Diagnostic]]:
    if isinstance(value, str):
        tokens = value.split()
    elif isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        tokens = list(value)
    else:
        return (), [_error(path, "ожидается непустая строка или список аргументов")]
    if "{config}" in " ".join(tokens):
        return (), [_error(path, "подстановка {config} пока не поддерживается: нужен список аргументов")]
    return tuple(tokens), []


def run_gates(gates: list[Gate], root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for gate in gates:
        diagnostics.extend(_run_gate(gate, root))
    return diagnostics


def _run_gate(gate: Gate, root: Path) -> list[Diagnostic]:
    if gate.binary:
        version, error = probe_binary(gate.binary, root)
        if error:
            return [Diagnostic("gate", "gap", gate.id, f"гейт {gate.id}: {gate.binary} недоступен: {error}")]
        if gate.version:
            if version is None:
                return [Diagnostic("gate", "gap", gate.id, f"гейт {gate.id}: версия {gate.binary} не определена")]
            if not satisfies(version, gate.version):
                return [
                    Diagnostic(
                        "gate",
                        "gap",
                        gate.id,
                        f"гейт {gate.id}: версия {version} не подходит под {gate.version}",
                    )
                ]

    argv = [token.replace("{project}", str(root)) for token in gate.argv]
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GATE_TIMEOUT,
        )
    except FileNotFoundError:
        return [Diagnostic("gate", "gap", gate.id, f"гейт {gate.id}: команда не найдена: {argv[0]}")]
    except subprocess.TimeoutExpired:
        return [Diagnostic("gate", "error", gate.id, f"гейт {gate.id}: не завершился за {GATE_TIMEOUT} с")]
    except OSError as error:
        return [Diagnostic("gate", "error", gate.id, f"гейт {gate.id}: команда не запущена: {error}")]

    output = _first_line(completed.stderr) or _first_line(completed.stdout)
    suffix = f": {output}" if output else ""
    if completed.returncode == 0:
        return []
    if completed.returncode == 1:
        return [Diagnostic("gate", "error", gate.id, f"гейт {gate.id}: нарушение{suffix}")]
    if completed.returncode == 2:
        return [Diagnostic("gate", "error", gate.id, f"гейт {gate.id}: ошибка конфигурации{suffix}")]
    if completed.returncode == 3:
        return [Diagnostic("gate", "gap", gate.id, f"гейт {gate.id}: не реализован{suffix}")]
    return [
        Diagnostic("gate", "error", gate.id, f"гейт {gate.id}: неожиданный код {completed.returncode}{suffix}")
    ]


def probe_binary(binary: str, root: Path) -> tuple[str | None, str | None]:
    try:
        completed = subprocess.run(
            [binary, "--version"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT,
        )
    except FileNotFoundError:
        return None, "команда не найдена"
    except subprocess.TimeoutExpired:
        return None, f"версия не ответила за {PROBE_TIMEOUT} с"
    except OSError as error:
        return None, str(error)
    text = completed.stdout or completed.stderr
    return parse_version(text), None


def parse_version(text: str) -> str | None:
    match = VERSION.search(text)
    return match.group(0) if match else None


def satisfies(version: str, spec: str) -> bool:
    left = _padded(version)
    for condition in spec.split():
        match = CONDITION.fullmatch(condition)
        if match is None:
            raise ValueError(condition)
        operator = match.group(1) or "=="
        right = _padded(match.group(2))
        if operator == "==" and left != right:
            return False
        if operator == ">=" and left < right:
            return False
        if operator == ">" and left <= right:
            return False
        if operator == "<=" and left > right:
            return False
        if operator == "<" and left >= right:
            return False
    return True


def _padded(version: str) -> tuple[int, ...]:
    match = VERSION.search(version)
    if match is None:
        raise ValueError(version)
    parts = [int(part) for part in match.group(0).split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:OUTPUT_LIMIT]
    return ""


def _error(path: str, message: str) -> Diagnostic:
    return Diagnostic("gate", "error", path, message)
