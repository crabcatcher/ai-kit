"""Генератор init: профиль из проб и ответов, документы из шаблонов."""

from __future__ import annotations

import copy
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_kit import ask as prompt
from ai_kit import baseline, interview, precommit, profile, rules, templates, toml_writer
from ai_kit.console import configure_console

DEFAULT_DOCS_DIR = ".project"
DEFAULT_LANGUAGE = "ru"
DEFAULT_SCHEMA = "1"
NO_VALUE = "—"
PROFILE_ORDER = (
    "schema_version", "project", "docs", "stack", "layout", "tests",
    "process", "automation", "security", "rules", "gates", "unknowns", "adapter",
)
FRAGMENT_MARKER = re.compile(r"^adapter:[a-z0-9][a-z0-9-]*$")


class InitError(Exception):
    pass


@dataclass(frozen=True)
class Request:
    target: Path
    kit_root: Path
    profile: Path | None = None
    adapter: Path | None = None
    answers: Path | None = None
    write_profile: str = "profile.toml"
    write_baseline: str = "baseline.toml"
    dry_run: bool = False
    ask: bool = False
    yes: bool = False


@dataclass(frozen=True)
class Report:
    profile: str
    documents: tuple[str, ...]
    unknowns: int
    dry_run: bool
    cancelled: bool = False
    baseline: str | None = None
    precommit: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    documents: list[templates.Document]
    values: dict[str, Any]
    fragments: dict[str, dict[str, str]]
    generated: dict[str, list[rules.Rule]]
    baseline: list[baseline.Violation]


def generate(request: Request) -> Report:
    documents = templates.load_documents(request.kit_root / "documents.toml", request.kit_root)
    values = _load_toml(request.profile) if request.profile else {}
    adapter = _load_toml(request.adapter) if request.adapter else {}
    _apply_commands(values, adapter)

    result = _run_interview(values, adapter, request)
    data = _build_profile(result.values, result.unknowns, adapter.get("id"))
    try:
        parsed = profile.parse(data)
    except profile.ProfileError as error:
        raise InitError(f"профиль не собран:\n{error}") from error

    canon = _canon_rules(request.kit_root)
    adapter_rules = _adapter_rules(adapter)
    baseline_path = _safe_path(request.target, request.write_baseline, "baseline")
    baseline_items = baseline.load(baseline_path)
    plan = Plan(
        documents=documents,
        values=data,
        fragments=_index_adapter_fragments(documents, data, adapter),
        generated={
            "canon": canon,
            "adapter": adapter_rules,
            "project": parsed.rules,
        },
        baseline=baseline_items,
    )

    profile_text = toml_writer.dumps(data)
    outputs = _render_all(plan, request)
    profile_path = _safe_path(request.target, request.write_profile, "профиль")
    artifacts = _artifacts(data, parsed, request)

    if request.ask and not request.yes:
        print(f"Профиль: {_display(request.target, profile_path)}")
        print(f"Документов: {len(outputs)}")
        if artifacts:
            print(f"Артефактов: {len(artifacts)}")
        if not prompt.confirm():
            return Report(
                profile=_display(request.target, profile_path),
                documents=tuple(_display(request.target, path) for path, _ in outputs),
                unknowns=len(data.get("unknowns", [])),
                dry_run=True,
                cancelled=True,
                baseline=_display(request.target, baseline_path) if baseline_path.exists() else None,
                precommit=tuple(_display(request.target, path) for path, _ in artifacts if "pre-commit" in path.name),
            )

    if not request.dry_run:
        _write(profile_path, profile_text)
        for path, text in outputs:
            _write(path, text)
        for path, text in artifacts:
            _write(path, text)

    return Report(
        profile=_display(request.target, profile_path),
        documents=tuple(_display(request.target, path) for path, _ in outputs),
        unknowns=len(data.get("unknowns", [])),
        dry_run=request.dry_run,
        baseline=_display(request.target, baseline_path)
        if baseline_path.exists() or any(path == baseline_path for path, _ in artifacts)
        else None,
        precommit=tuple(_display(request.target, path) for path, _ in artifacts if "pre-commit" in path.name),
    )


def _artifacts(
    data: dict[str, Any],
    parsed: profile.Profile,
    request: Request,
) -> list[tuple[Path, str]]:
    artifacts: list[tuple[Path, str]] = []
    baseline_path = _safe_path(request.target, request.write_baseline, "baseline")
    if not baseline_path.exists():
        artifacts.append((baseline_path, baseline.TEMPLATE))
    if parsed.automation.pre_commit:
        generated = precommit.files(data)
        if not generated:
            raise InitError("pre_commit=true, но нет команд unit, lint или test")
        for relative, text in generated.items():
            artifacts.append((_safe_path(request.target, relative, relative), text))
    return artifacts


def _run_interview(values: dict[str, Any], adapter: dict[str, Any], request: Request) -> interview.InterviewResult:
    universal = interview.load_questions(request.kit_root / "interview" / "questions.toml")
    bank = interview.merge_questions(universal, interview.load_adapter_questions(adapter))
    prepared = interview.prepare(values, request.target, interview.load_probes(adapter), bank)
    if request.answers:
        answers = interview.load_answers(request.answers)
    elif request.ask:
        answers = prompt.collect(list(prepared.batch), prepared.values)
    else:
        answers = []
    return interview.finish(prepared, answers)


def _apply_commands(data: dict[str, Any], adapter: dict[str, Any]) -> None:
    entries = adapter.get("commands", [])
    if not entries:
        return
    if not isinstance(entries, list):
        raise InitError("commands: ожидается список")
    tests = data.setdefault("tests", {})
    if not isinstance(tests, dict):
        raise InitError("tests: ожидается объект")
    commands = tests.setdefault("commands", {})
    if not isinstance(commands, dict):
        raise InitError("tests.commands: ожидается объект")
    seen: set[str] = set()
    for position, entry in enumerate(entries):
        place = f"commands[{position}]"
        if not isinstance(entry, dict):
            raise InitError(f"{place}: ожидается объект")
        extra = set(entry) - {"role", "command"}
        if extra:
            raise InitError(f"{place}: неизвестные поля: {', '.join(sorted(extra))}")
        role = entry.get("role")
        command = entry.get("command")
        if role not in templates.COMMAND_ROLES:
            raise InitError(f"{place}.role: ожидается одно из: {', '.join(templates.COMMAND_ROLES)}")
        if not isinstance(command, str) or not command.strip():
            raise InitError(f"{place}.command: ожидается непустая строка")
        if role in seen:
            raise InitError(f"{place}.role: роль повторяется")
        seen.add(role)
        commands.setdefault(role, command)


def _build_profile(
    values: dict[str, Any],
    unknowns: list[dict[str, str]],
    adapter_id: str | None,
) -> dict[str, Any]:
    data = copy.deepcopy(values)
    data.setdefault("schema_version", DEFAULT_SCHEMA)
    data.setdefault("stack", {})
    data.setdefault("tests", {})
    docs = data.setdefault("docs", {})
    if not isinstance(docs, dict):
        raise InitError("docs: ожидается объект")
    docs.setdefault("language", DEFAULT_LANGUAGE)
    docs.setdefault("dir", DEFAULT_DOCS_DIR)

    merged = _merge_unknowns(data, unknowns)
    if merged:
        data["unknowns"] = merged
    else:
        data.pop("unknowns", None)
    if adapter_id:
        adapter = data.setdefault("adapter", {})
        if not isinstance(adapter, dict):
            raise InitError("adapter: ожидается объект")
        adapter["id"] = adapter_id

    return {key: data[key] for key in PROFILE_ORDER if key in data} | {
        key: value for key, value in data.items() if key not in PROFILE_ORDER
    }


def _merge_unknowns(
    data: dict[str, Any],
    fresh: list[dict[str, str]],
) -> list[dict[str, str]]:
    existing = data.get("unknowns", [])
    if not isinstance(existing, list):
        raise InitError("unknowns: ожидается список")
    for index, entry in enumerate(existing):
        if not isinstance(entry, dict) or not isinstance(entry.get("field"), str):
            raise InitError(f"unknowns[{index}]: ожидается объект с полем field")
    merged: dict[str, dict[str, str]] = {entry["field"]: entry for entry in existing}
    merged.update({entry["field"]: entry for entry in fresh})
    return [entry for field, entry in merged.items() if not interview.is_known(data, field)]


def _canon_rules(kit_root: Path) -> list[rules.Rule]:
    try:
        rows = rules.load_canon(kit_root / "canon" / "registry.md")
    except (OSError, ValueError) as error:
        raise InitError(f"канон не прочитан: {error}") from error
    if not rows:
        raise InitError("канон не содержит строк K")
    return rows


def _adapter_rules(adapter: dict[str, Any]) -> list[rules.Rule]:
    entries = adapter.get("rules", [])
    errors = rules.rule_errors(entries, "A", "rules")
    if errors:
        raise InitError("\n".join(f"{path}: {message}" for path, message in errors))
    return rules.build_rules(entries)


def _index_adapter_fragments(
    documents: list[templates.Document],
    values: dict[str, Any],
    adapter: dict[str, Any],
) -> dict[str, dict[str, str]]:
    paths = {document.id: templates.document_path(document, values) for document in documents}
    index: dict[str, dict[str, str]] = {}
    for position, entry in enumerate(adapter.get("docs_fragments", [])):
        place = f"docs_fragments[{position}]"
        if not isinstance(entry, dict):
            raise InitError(f"{place}: ожидается объект")
        extra = set(entry) - {"target", "marker", "content"}
        if extra:
            raise InitError(f"{place}: неизвестные поля: {', '.join(sorted(extra))}")
        target = entry.get("target")
        marker = entry.get("marker")
        content = entry.get("content")
        if not isinstance(target, str) or not target:
            raise InitError(f"{place}.target: ожидается непустая строка")
        if not isinstance(marker, str) or FRAGMENT_MARKER.fullmatch(marker) is None:
            raise InitError(f"{place}.marker: ожидается adapter:<имя>")
        if not isinstance(content, str):
            raise InitError(f"{place}.content: ожидается строка")

        matches = [
            path
            for document_id, path in paths.items()
            if target in (document_id, path, _relative_to_docs(path, values))
        ]
        if len(matches) != 1:
            raise InitError(f"{place}.target: {target} не указывает ровно один документ")
        bucket = index.setdefault(matches[0], {})
        if marker in bucket:
            raise InitError(f"{place}.marker: {marker} задан дважды для {target}")
        bucket[marker] = content
    return index


def _relative_to_docs(path: str, values: dict[str, Any]) -> str:
    docs_dir = values.get("docs", {}).get("dir")
    if not isinstance(docs_dir, str) or not docs_dir:
        return path
    prefix = docs_dir.rstrip("/") + "/"
    return path[len(prefix):] if path.startswith(prefix) else path


def _render_all(plan: Plan, request: Request) -> list[tuple[Path, str]]:
    outputs: list[tuple[Path, str]] = []
    for document in plan.documents:
        relative = templates.document_path(document, plan.values)
        path = _safe_path(request.target, relative, document.id)
        source = (request.kit_root / document.template).read_text(encoding="utf-8")
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        fragments = _document_fragments(document, source, existing, plan)
        context = templates.RenderContext(
            document=document,
            documents=plan.documents,
            root=request.kit_root,
            values=plan.values,
            commands=plan.values.get("tests", {}).get("commands", {}),
            fragments=fragments,
        )
        outputs.append((path, templates.render_document(context)))
    return outputs


def _document_fragments(
    document: templates.Document,
    source: str,
    existing: str | None,
    plan: Plan,
) -> dict[str, str]:
    fragments: dict[str, str] = dict(plan.fragments.get(templates.document_path(document, plan.values), {}))
    for name in templates.marker_names(source, "adapter"):
        key = f"adapter:{name}"
        if key in fragments:
            continue
        body = _marker_body(existing, key)
        if body:
            fragments[key] = body

    rows = plan.generated["canon"] + plan.generated["adapter"] + plan.generated["project"]
    providers = {
        "canon": lambda: _table(plan.generated["canon"]),
        "adapter": lambda: _table(plan.generated["adapter"]),
        "project": lambda: _table(plan.generated["project"]),
        "no-gate": lambda: _no_gate_table(rows),
        "rules": lambda: _bullets(rows),
        "baseline": lambda: _baseline_table(plan.baseline),
    }
    for name in templates.marker_names(source, "generated"):
        provider = providers.get(name)
        if provider is None:
            continue
        text = provider()
        if text:
            fragments[f"generated:{name}"] = text
    return fragments


def _marker_body(existing: str | None, key: str) -> str | None:
    if not existing:
        return None
    pattern = re.compile(
        rf"<!-- {re.escape(key)}:begin -->\n?(.*?)\n?<!-- {re.escape(key)}:end -->",
        re.DOTALL,
    )
    match = pattern.search(existing)
    if match is None:
        return None
    body = match.group(1).strip("\n")
    return body or None


def _table(rows: list[rules.Rule]) -> str:
    return "\n".join(
        f"| {_cell(row.id)} | {_cell(row.text)} | {_cell(row.label)} | {_cell(row.executor)} | "
        f"{_cell(row.catcher)} | {_cell(row.owner)} | {_cell(row.trigger)} |"
        for row in rows
    )


def _no_gate_table(rows: list[rules.Rule]) -> str:
    return "\n".join(
        f"| {_cell(row.id)} | {_cell(row.text)} | {_cell(row.reason or NO_VALUE)} | "
        f"{_cell(row.catcher)} | {_cell(row.owner)} | {_cell(row.trigger)} |"
        for row in rows
        if row.label == "нет гейта"
    )


def _bullets(rows: list[rules.Rule]) -> str:
    return "\n".join(f"- {row.id} — {row.text};" for row in rows)


def _baseline_table(rows: list[baseline.Violation]) -> str:
    if not rows:
        return ""
    header = "| Правило | Где | Почему | Владелец | Триггер |\n|---|---|---|---|---|"
    body = "\n".join(
        f"| {_cell(row.rule)} | {_cell(row.path)} | {_cell(row.reason)} | {_cell(row.owner)} | {_cell(row.trigger)} |"
        for row in rows
    )
    return f"{header}\n{body}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _safe_path(target: Path, relative: str, what: str) -> Path:
    root = target.resolve()
    candidate = (target / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise InitError(f"{what}: путь выходит за пределы проекта: {relative}") from error
    return candidate


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _display(target: Path, path: Path) -> str:
    try:
        return path.relative_to(target.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _option(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    return args[index + 1] if index + 1 < len(args) else ""


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = sys.argv[1:] if argv is None else argv
    if "--help" in args:
        print(__doc__)
        return 0

    profile_path = _option(args, "--profile")
    adapter_path = _option(args, "--adapter")
    answers_path = _option(args, "--answers")
    write_profile = _option(args, "--write-profile")
    if write_profile is None:
        write_profile = "profile.toml"
    if profile_path == "" or adapter_path == "" or answers_path == "" or write_profile == "":
        print("после --profile, --adapter, --answers и --write-profile нужен путь", file=sys.stderr)
        return 2
    if not profile_path and not answers_path and "--ask" not in args:
        print("нужен --profile, --answers или --ask", file=sys.stderr)
        return 2

    request = Request(
        target=Path.cwd(),
        kit_root=Path(__file__).resolve().parents[1],
        profile=Path(profile_path) if profile_path else None,
        adapter=Path(adapter_path) if adapter_path else None,
        answers=Path(answers_path) if answers_path else None,
        write_profile=write_profile,
        dry_run="--dry-run" in args,
        ask="--ask" in args,
        yes="--yes" in args,
    )
    try:
        report = generate(request)
    except (
        OSError,
        tomllib.TOMLDecodeError,
        InitError,
        interview.InterviewError,
        prompt.AskAborted,
        templates.TemplateError,
        toml_writer.TomlWriteError,
    ) as error:
        print(f"генерация остановлена: {error}", file=sys.stderr)
        return 1

    print(f"Профиль: {report.profile}")
    print(f"Документов: {len(report.documents)}")
    print(f"Неизвестных: {report.unknowns}")
    if report.cancelled:
        print("Запись отменена.")
    elif report.dry_run:
        print("Прогон без записи: файлы не изменены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
