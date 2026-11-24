"""Генератор init: профиль из проб и ответов, документы из шаблонов."""

from __future__ import annotations

import copy
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_kit import ask as prompt
from ai_kit import baseline, detect, inputs, interview, precommit, profile, rules, templates, toml_writer
from ai_kit.args import UsageError, ensure_known, normalize
from ai_kit.console import configure_console
from ai_kit.paths import KitRootError, kit_root
from ai_kit.toml_io import TomlError, load as load_toml

DEFAULT_DOCS_DIR = ".project"
DEFAULT_LANGUAGE = "ru"
DEFAULT_SCHEMA = "1"
NO_VALUE = "—"
PROFILE_ORDER = (
    "schema_version", "project", "docs", "stack", "layout", "inputs", "tests",
    "process", "automation", "security", "rules", "gates", "unknowns",
    "not_applicable", "adapter",
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
    refresh: bool = False


@dataclass(frozen=True)
class Report:
    profile: str
    documents: tuple[str, ...]
    unknowns: int
    dry_run: bool
    cancelled: bool = False
    baseline: str | None = None
    precommit: tuple[str, ...] = ()
    overwritten: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    documents: list[templates.Document]
    values: dict[str, Any]
    fragments: dict[str, dict[str, str]]
    generated: dict[str, list[rules.Rule]]
    baseline: list[baseline.Violation]
    features: list[tuple[str, str]]
    gaps: list[dict[str, str]]


def generate(request: Request) -> Report:
    if request.refresh:
        return _refresh(request)

    values = _load_toml(request.profile) if request.profile else {}
    adapter = _load_toml(request.adapter) if request.adapter else {}
    _detect_preflight(adapter)
    values = interview.apply_commands(values, adapter)
    try:
        values = inputs.apply(values, adapter)
    except inputs.InputError as error:
        raise InitError(str(error)) from error

    result = _run_interview(values, adapter, request)
    data = _build_profile(result.values, result.unknowns, adapter.get("id"))
    try:
        parsed = profile.parse(data)
    except profile.ProfileError as error:
        raise InitError(f"профиль не собран:\n{error}") from error

    plan = _plan(request, data, parsed, adapter)
    profile_text = toml_writer.dumps(data)
    artifacts = _artifacts(data, parsed, request)
    outputs = _render_all(plan, request)
    profile_path = _safe_path(request.target, request.write_profile, "профиль")
    overwritten = _overwritten(request.target, [(profile_path, profile_text), *outputs, *artifacts])

    if request.ask and not request.yes:
        print(f"Профиль: {_display(request.target, profile_path)}")
        print(f"Документов: {len(outputs)}")
        if artifacts:
            print(f"Артефактов: {len(artifacts)}")
        if overwritten:
            print(f"Перезаписано: {', '.join(overwritten)}")
        if not prompt.confirm():
            return Report(
                profile=_display(request.target, profile_path),
                documents=tuple(_display(request.target, path) for path, _ in outputs),
                unknowns=len(data.get("unknowns", [])),
                dry_run=True,
                cancelled=True,
                baseline=_display(request.target, plan_baseline_path(request)) if plan_baseline_path(request).exists() else None,
                precommit=tuple(_display(request.target, path) for path, _ in artifacts if "pre-commit" in path.name),
                overwritten=overwritten,
            )

    if not request.dry_run:
        _write_all([(profile_path, profile_text), *outputs, *artifacts])

    return Report(
        profile=_display(request.target, profile_path),
        documents=tuple(_display(request.target, path) for path, _ in outputs),
        unknowns=len(data.get("unknowns", [])),
        dry_run=request.dry_run,
        baseline=_display(request.target, plan_baseline_path(request))
        if plan_baseline_path(request).exists() or any(path == plan_baseline_path(request) for path, _ in artifacts)
        else None,
        precommit=tuple(_display(request.target, path) for path, _ in artifacts if "pre-commit" in path.name),
        overwritten=overwritten,
    )


def _refresh(request: Request) -> Report:
    if not request.profile:
        raise InitError("--refresh требует --profile: профиль не создаётся заново")
    data = copy.deepcopy(_load_toml(request.profile))
    adapter = _load_toml(request.adapter) if request.adapter else {}
    try:
        parsed = profile.parse(data)
    except profile.ProfileError as error:
        raise InitError(f"профиль не прошёл проверку:\n{error}") from error

    plan = _plan(request, data, parsed, adapter)
    outputs = _render_all(plan, request)
    profile_path = request.profile
    overwritten = _overwritten(request.target, outputs)
    if not request.dry_run:
        _write_all(outputs)
    return Report(
        profile=_display(request.target, profile_path),
        documents=tuple(_display(request.target, path) for path, _ in outputs),
        unknowns=len(data.get("unknowns", [])),
        dry_run=request.dry_run,
        baseline=_display(request.target, plan_baseline_path(request))
        if plan_baseline_path(request).exists()
        else None,
        overwritten=overwritten,
    )


def _plan(
    request: Request,
    data: dict[str, Any],
    parsed: profile.Profile,
    adapter: dict[str, Any],
) -> Plan:
    documents = templates.load_documents(request.kit_root / "documents.toml", request.kit_root)
    try:
        items = baseline.load(plan_baseline_path(request))
    except (baseline.BaselineError, OSError, TomlError) as error:
        raise InitError(f"baseline не прочитан:\n{error}") from error
    return Plan(
        documents=documents,
        values=data,
        fragments=_index_adapter_fragments(documents, data, adapter, request),
        generated={
            "canon": _canon_rules(request.kit_root),
            "adapter": _adapter_rules(adapter),
            "project": parsed.rules,
        },
        baseline=items,
        features=_feature_rows(request.target, data),
        gaps=_gap_rows(adapter),
    )


def plan_baseline_path(request: Request) -> Path:
    return _safe_path(request.target, request.write_baseline, "baseline")


def _overwritten(target: Path, files: list[tuple[Path, str]]) -> tuple[str, ...]:
    result: list[str] = []
    for path, text in files:
        if not path.exists():
            continue
        try:
            current = path.read_bytes()
        except OSError:
            result.append(_display(target, path))
            continue
        if current != text.encode("utf-8"):
            result.append(_display(target, path))
    return tuple(result)


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
            raise InitError("pre_commit=true, но нет команд unit, lint, test или build")
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
    not_applicable = interview.not_applicable_fields(data)
    return [
        entry
        for field, entry in merged.items()
        if field not in not_applicable and not interview.is_known(data, field)
    ]


def _canon_rules(kit_root: Path) -> list[rules.Rule]:
    try:
        rows = rules.load_canon(kit_root / "canon" / "registry.md")
    except (OSError, ValueError) as error:
        raise InitError(f"канон не прочитан: {error}") from error
    if not rows:
        raise InitError("канон не содержит строк K")
    return rows


def _detect_preflight(adapter: dict[str, Any]) -> None:
    _, errors = detect.load(adapter)
    if errors:
        raise InitError("detect: " + "; ".join(f"{path}: {message}" for path, message in errors))


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
    request: Request,
) -> dict[str, dict[str, str]]:
    paths = {document.id: templates.document_path(document, values) for document in documents}
    by_path = {templates.document_path(document, values): document for document in documents}
    index: dict[str, dict[str, str]] = {}
    for position, entry in enumerate(adapter.get("docs_fragments", [])):
        place = f"docs_fragments[{position}]"
        if not isinstance(entry, dict):
            raise InitError(f"{place}: ожидается объект")
        extra = set(entry) - {"target", "marker", "content", "content_file"}
        if extra:
            raise InitError(f"{place}: неизвестные поля: {', '.join(sorted(extra))}")
        target = entry.get("target")
        marker = entry.get("marker")
        content = _fragment_content(entry, place, request)
        if not isinstance(target, str) or not target:
            raise InitError(f"{place}.target: ожидается непустая строка")
        if not isinstance(marker, str) or FRAGMENT_MARKER.fullmatch(marker) is None:
            raise InitError(f"{place}.marker: ожидается adapter:<имя>")

        matches = [
            path
            for document_id, path in paths.items()
            if target in (document_id, path, _relative_to_docs(path, values))
        ]
        if len(matches) > 1:
            raise InitError(f"{place}.target: {target} не указывает ровно один документ")
        if matches:
            relative = matches[0]
            if "/templates/" in relative:
                raise InitError(
                    f"{place}.target: {target} — шаблон документа, а не документ проекта; "
                    "docs_fragments туда не целятся"
                )
            document = by_path[relative]
            source = (request.kit_root / document.template).read_text(encoding="utf-8")
            name = marker.split(":", 1)[1]
            if name not in templates.marker_names(source, "adapter"):
                raise InitError(f"{place}.marker: маркера {marker} нет в шаблоне {document.template}")
        else:
            relative = _feature_target(target, place, values, request)
            if relative is None:
                raise InitError(f"{place}.target: {target} не указывает ровно один документ")
        bucket = index.setdefault(relative, {})
        if marker in bucket:
            raise InitError(f"{place}.marker: {marker} задан дважды для {target}")
        bucket[marker] = content
    return index


def _feature_target(
    target: str,
    place: str,
    values: dict[str, Any],
    request: Request,
) -> str | None:
    docs_dir = values.get("docs", {}).get("dir")
    if not isinstance(docs_dir, str) or not docs_dir or "\\" in target:
        return None
    prefix = docs_dir.rstrip("/") + "/"
    name = target[len(prefix):] if target.startswith(prefix) else target
    parts = name.split("/")
    if len(parts) != 2 or parts[0] != "features" or not parts[1].endswith(".md"):
        return None
    if re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*\.md", parts[1]) is None:
        return None
    relative = f"{docs_dir.rstrip('/')}/features/{parts[1]}"
    path = _safe_path(request.target, relative, "фрагмент")
    if not path.is_file():
        raise InitError(
            f"{place}.target: файла функции нет: {relative}; "
            "создайте его вручную или уберите docs_fragments"
        )
    return relative


def _fragment_content(entry: dict[str, Any], place: str, request: Request) -> str:
    has_content = "content" in entry
    has_file = "content_file" in entry
    if has_content == has_file:
        raise InitError(f"{place}: нужен ровно один из content и content_file")
    if has_content:
        content = entry["content"]
        if not isinstance(content, str):
            raise InitError(f"{place}.content: ожидается строка")
        return content

    name = entry["content_file"]
    if not isinstance(name, str) or not name.strip():
        raise InitError(f"{place}.content_file: ожидается непустой путь")
    if Path(name).is_absolute() or re.match(r"^[A-Za-z]:", name):
        raise InitError(f"{place}.content_file: путь должен быть относительным: {name}")
    base = request.target
    if request.adapter is not None:
        base = request.target / request.adapter.parent
    path = (base / name).resolve()
    try:
        path.relative_to(request.target.resolve())
    except ValueError as error:
        raise InitError(f"{place}.content_file: путь выходит за каталог проекта: {name}") from error
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise InitError(f"{place}.content_file: файл не читается: {name}: {error}") from error
    except UnicodeDecodeError as error:
        raise InitError(f"{place}.content_file: файл не в UTF-8: {name}") from error


def _relative_to_docs(path: str, values: dict[str, Any]) -> str:
    docs_dir = values.get("docs", {}).get("dir")
    if not isinstance(docs_dir, str) or not docs_dir:
        return path
    prefix = docs_dir.rstrip("/") + "/"
    return path[len(prefix):] if path.startswith(prefix) else path


def _render_all(plan: Plan, request: Request) -> list[tuple[Path, str]]:
    outputs: list[tuple[Path, str]] = []
    rendered: set[str] = set()
    for document in plan.documents:
        relative = templates.document_path(document, plan.values)
        rendered.add(relative)
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

    for relative, fragments in plan.fragments.items():
        if relative in rendered:
            continue
        path = _safe_path(request.target, relative, "фрагмент")
        if not path.is_file():
            raise InitError(f"файла функции нет: {relative}; создайте его вручную или уберите docs_fragments")
        raw = path.read_bytes()
        try:
            existing = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise InitError(f"файл функции не в UTF-8: {relative}") from error
        first = existing.find("\n")
        newline = "\r\n" if first > 0 and existing[first - 1] == "\r" else "\n"
        text = templates.fill_fragments(existing, fragments, where=relative, newline=newline)
        if text == existing:
            continue
        if raw.startswith(b"\xef\xbb\xbf"):
            text = "\ufeff" + text
        outputs.append((path, text))
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
        "features": lambda: _features_table(plan.features),
        "gaps": lambda: _gaps_table(plan.gaps),
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


def _feature_rows(target: Path, values: dict[str, Any]) -> list[tuple[str, str]]:
    docs_dir = values.get("docs", {}).get("dir")
    if not isinstance(docs_dir, str) or not docs_dir:
        return []
    folder = target / docs_dir / "features"
    if not folder.is_dir():
        return []
    rows: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.md")):
        title = path.stem
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        rows.append((title, f"features/{path.name}"))
    return rows


def _features_table(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"| {_cell(title)} | `{_cell(path)}` |" for title, path in rows)


def _gap_rows(adapter: dict[str, Any]) -> list[dict[str, str]]:
    entries = adapter.get("gaps", [])
    if not isinstance(entries, list):
        raise InitError("gaps: ожидается список")
    rows: list[dict[str, str]] = []
    for position, entry in enumerate(entries):
        place = f"gaps[{position}]"
        if not isinstance(entry, dict):
            raise InitError(f"{place}: ожидается объект")
        extra = set(entry) - {"id", "reason", "owner", "trigger"}
        if extra:
            raise InitError(f"{place}: неизвестные поля: {', '.join(sorted(extra))}")
        for key in ("id", "reason", "owner", "trigger"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                raise InitError(f"{place}.{key}: ожидается непустая строка")
        rows.append(entry)
    return rows


def _gaps_table(rows: list[dict[str, str]]) -> str:
    return "\n".join(
        f"| {_cell(row['id'])} | {_cell(row['reason'])} | {_cell(row['owner'])} | {_cell(row['trigger'])} |"
        for row in rows
    )


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


def _write_all(files: list[tuple[Path, str]]) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for path, text in files:
            desired = text.encode("utf-8")
            if path.is_file():
                try:
                    if path.read_bytes() == desired:
                        if path.name == "pre-commit" and path.parent.name == "hooks":
                            try:
                                path.chmod(0o755)
                            except OSError:
                                pass
                        continue
                except OSError:
                    pass
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            staged.append((Path(temporary), path))
        for temporary, path in staged:
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
            os.chmod(temporary, mode)
            os.replace(temporary, path)
            if path.name == "pre-commit" and path.parent.name == "hooks":
                path.chmod(0o755)
    finally:
        for temporary, _ in staged:
            if temporary.exists():
                temporary.unlink()


def _display(target: Path, path: Path) -> str:
    try:
        return path.relative_to(target.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_toml(path: Path) -> dict[str, Any]:
    return load_toml(path)


def _option(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    return args[index + 1] if index + 1 < len(args) else ""


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = normalize(sys.argv[1:] if argv is None else argv)
    try:
        ensure_known(
            args,
            {
                "--profile", "--adapter", "--answers", "--write-profile", "--write-baseline",
                "--dry-run", "--ask", "--yes", "--refresh", "--help",
            },
        )
    except UsageError as error:
        print(f"{error}\nИспользование: python -m ai_kit.init --help", file=sys.stderr)
        return 2
    if "--help" in args:
        print(__doc__)
        return 0

    profile_path = _option(args, "--profile")
    adapter_path = _option(args, "--adapter")
    answers_path = _option(args, "--answers")
    write_profile = _option(args, "--write-profile")
    if write_profile is None:
        write_profile = "profile.toml"
    write_baseline = _option(args, "--write-baseline")
    if write_baseline is None:
        write_baseline = "baseline.toml"
    if profile_path == "" or adapter_path == "" or answers_path == "" or write_profile == "" or write_baseline == "":
        print("после --profile, --adapter, --answers, --write-profile и --write-baseline нужен путь", file=sys.stderr)
        return 2
    if not profile_path and not answers_path and "--ask" not in args:
        print("нужен --profile, --answers или --ask", file=sys.stderr)
        return 2
    if "--refresh" in args and (answers_path or "--ask" in args):
        print("--refresh несовместим с --answers и --ask", file=sys.stderr)
        return 2
    if "--refresh" in args and not profile_path:
        print("--refresh требует --profile", file=sys.stderr)
        return 2

    try:
        root = kit_root()
    except KitRootError as error:
        print(error, file=sys.stderr)
        return 2

    request = Request(
        target=Path.cwd(),
        kit_root=root,
        profile=Path(profile_path) if profile_path else None,
        adapter=Path(adapter_path) if adapter_path else None,
        answers=Path(answers_path) if answers_path else None,
        write_profile=write_profile,
        write_baseline=write_baseline,
        dry_run="--dry-run" in args,
        ask="--ask" in args,
        yes="--yes" in args,
        refresh="--refresh" in args,
    )
    try:
        report = generate(request)
    except (
        OSError,
        TomlError,
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
    if report.overwritten:
        print(f"Перезаписано: {', '.join(report.overwritten)}")
    if report.cancelled:
        print("Запись отменена.")
    elif report.dry_run:
        print("Прогон без записи: файлы не изменены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
