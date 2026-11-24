"""Проверка адаптера до генерации: форма, ссылки и фрагменты одним проходом."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ai_kit import detect, gaps, gates, init, inputs, interview, rules, templates
from ai_kit.args import UsageError, ensure_known, normalize
from ai_kit.console import configure_console
from ai_kit.diagnostics import Diagnostic, Report
from ai_kit.paths import KitRootError, kit_root
from ai_kit.toml_io import TomlError, load as load_toml

TOP_KEYS = {
    "id", "detect", "probes", "commands", "gates", "inputs",
    "rules", "docs_fragments", "questions", "gaps",
}


def validate(
    adapter_path: Path,
    profile_path: Path | None,
    project: Path,
    *,
    run_probes: bool = False,
) -> Report:
    diagnostics: list[Diagnostic] = []
    try:
        adapter = load_toml(adapter_path)
    except (OSError, TomlError) as error:
        return Report((Diagnostic("adapter", "error", adapter_path.name, f"адаптер не читается: {error}"),))

    for key in sorted(set(adapter) - TOP_KEYS):
        diagnostics.append(Diagnostic("adapter", "error", key, "неизвестная секция адаптера"))
    if "id" in adapter and (not isinstance(adapter["id"], str) or not adapter["id"].strip()):
        diagnostics.append(Diagnostic("adapter", "error", "id", "ожидается непустая строка"))

    values: dict[str, Any] = {"docs": {"dir": init.DEFAULT_DOCS_DIR}}
    if profile_path is not None:
        try:
            values = load_toml(profile_path)
        except (OSError, TomlError) as error:
            diagnostics.append(
                Diagnostic("profile", "error", profile_path.name, f"профиль не читается: {error}")
            )
        else:
            values = _with_docs(values, diagnostics)

    try:
        root = kit_root()
        documents = templates.load_documents(root / "documents.toml", root)
    except (KitRootError, OSError, TomlError, templates.TemplateError) as error:
        return Report(tuple(diagnostics) + (Diagnostic("manifest", "error", "documents.toml", str(error)),))

    _detect(adapter, project, diagnostics)
    probes = _probes(adapter, diagnostics)
    if run_probes and probes:
        hits, misses = interview.run_probes(project, probes)
        for miss in misses:
            diagnostics.append(
                Diagnostic(
                    "probe",
                    "warning",
                    miss.field,
                    f"проба {miss.probe_id} не нашла значение: {miss.reason}",
                )
            )
    _commands(values, adapter, diagnostics)
    adapter_rules, rules_ok = _rules(adapter, diagnostics)
    _gates(adapter, adapter_rules, rules_ok, diagnostics)
    parsed_inputs = _inputs(values, adapter, diagnostics)
    _get_gaps(adapter, diagnostics)
    _questions(root, adapter, diagnostics)
    missing_features = _fragments(adapter, values, documents, adapter_path, project, root, diagnostics)
    _inputs_features(parsed_inputs, values, project, missing_features, diagnostics)
    unique: dict[tuple[str, str, str, str], Diagnostic] = {}
    for item in diagnostics:
        unique[(item.code, item.severity, item.path, item.message)] = item
    return Report(tuple(unique.values()))


def _with_docs(values: dict[str, Any], diagnostics: list[Diagnostic]) -> dict[str, Any]:
    docs = values.get("docs")
    if isinstance(docs, dict) and isinstance(docs.get("dir"), str) and docs["dir"].strip():
        return values
    diagnostics.append(
        Diagnostic(
            "profile",
            "warning",
            "docs.dir",
            f"каталог документов не задан; пути проверяются от {init.DEFAULT_DOCS_DIR}",
        )
    )
    result = dict(values)
    result["docs"] = {"dir": init.DEFAULT_DOCS_DIR}
    return result


def _detect(adapter: dict[str, Any], project: Path, diagnostics: list[Diagnostic]) -> None:
    patterns, problems = detect.load(adapter)
    diagnostics.extend(
        Diagnostic("adapter", "error", path, message) for path, message in problems
    )
    if patterns and not detect.matches(project, patterns):
        diagnostics.append(
            Diagnostic("adapter", "warning", "detect", "адаптер не узнаёт проект: ни одна маска не найдена")
        )


def _probes(adapter: dict[str, Any], diagnostics: list[Diagnostic]) -> list[interview.Probe]:
    try:
        probes = interview.parse_probes(adapter.get("probes", []))
    except interview.InterviewError as error:
        diagnostics.extend(
            Diagnostic("adapter", "error", path, message) for path, message in error.errors
        )
        return []

    scalar: dict[str, str] = {}
    facts: dict[tuple[str, str], str] = {}
    for probe in probes:
        node = interview.schema.resolve(probe.field)
        if node is None:
            continue
        if interview.schema.is_evidence(node):
            if probe.name is None:
                continue
            key = (probe.field, probe.name)
            if key in facts:
                diagnostics.append(
                    Diagnostic(
                        "adapter",
                        "error",
                        "probes",
                        f"две пробы на факт {probe.name}: {facts[key]}, {probe.id}",
                    )
                )
            else:
                facts[key] = probe.id
        else:
            if probe.field in scalar:
                diagnostics.append(
                    Diagnostic(
                        "adapter",
                        "error",
                        "probes",
                        f"две пробы на скалярное поле {probe.field}: {scalar[probe.field]}, {probe.id}",
                    )
                )
            else:
                scalar[probe.field] = probe.id
    return probes


def _commands(values: dict[str, Any], adapter: dict[str, Any], diagnostics: list[Diagnostic]) -> None:
    try:
        interview.apply_commands(values, adapter)
    except interview.InterviewError as error:
        diagnostics.extend(
            Diagnostic("adapter", "error", path, message) for path, message in error.errors
        )


def _rules(adapter: dict[str, Any], diagnostics: list[Diagnostic]) -> tuple[list[rules.Rule], bool]:
    entries = adapter.get("rules", [])
    problems = rules.rule_errors(entries, "A", "rules")
    diagnostics.extend(
        Diagnostic("adapter", "error", path, message) for path, message in problems
    )
    if problems:
        return [], False
    return rules.build_rules(entries), True


def _gates(
    adapter: dict[str, Any],
    adapter_rules: list[rules.Rule],
    rules_ok: bool,
    diagnostics: list[Diagnostic],
) -> None:
    gate_list, problems = gates.load_gates(adapter)
    diagnostics.extend(problems)
    if not rules_ok:
        return
    texts = {rule.text for rule in adapter_rules}
    for index, gate in enumerate(gate_list):
        if gate.invariant not in texts:
            diagnostics.append(
                Diagnostic(
                    "reference",
                    "error",
                    f"gates[{index}].invariant",
                    f"нет строки A с формулировкой «{gate.invariant}»",
                )
            )
    gate_texts = {gate.invariant for gate in gate_list}
    for rule in adapter_rules:
        if rule.label in ("гейт", "гейт частичный") and rule.text not in gate_texts:
            diagnostics.append(
                Diagnostic(
                    "reference",
                    "error",
                    f"rules/{rule.id}",
                    f"правило {rule.id} помечено «{rule.label}», но гейта нет",
                )
            )


def _inputs(
    values: dict[str, Any],
    adapter: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> list[inputs.Input]:
    problems = inputs.errors(adapter.get("inputs", []))
    diagnostics.extend(
        Diagnostic("adapter", "error", path, message) for path, message in problems
    )
    if problems:
        return []
    parsed = inputs.parse(adapter.get("inputs", []))
    if "inputs" not in values:
        return parsed
    try:
        existing = inputs.parse(values["inputs"])
    except inputs.InputError as error:
        diagnostics.extend(
            Diagnostic("profile", "error", path, message) for path, message in error.errors
        )
        return parsed
    if "inputs" in adapter and sorted(existing, key=lambda item: item.id) != sorted(
        parsed, key=lambda item: item.id
    ):
        diagnostics.append(
            Diagnostic(
                "reference",
                "error",
                "inputs",
                "inputs профиля и адаптера расходятся: профиль — источник истины",
            )
        )
    return existing


def _get_gaps(adapter: dict[str, Any], diagnostics: list[Diagnostic]) -> None:
    diagnostics.extend(
        Diagnostic("adapter", "error", path, message)
        for path, message in gaps.errors(adapter.get("gaps", []))
    )


def _questions(root: Path, adapter: dict[str, Any], diagnostics: list[Diagnostic]) -> None:
    try:
        universal = interview.load_questions(root / "interview" / "questions.toml")
        adapter_questions = interview.load_adapter_questions(adapter)
        interview.merge_questions(universal, adapter_questions)
    except (OSError, TomlError, interview.InterviewError) as error:
        diagnostics.append(Diagnostic("adapter", "error", "questions", str(error)))


def _fragments(
    adapter: dict[str, Any],
    values: dict[str, Any],
    documents: list[templates.Document],
    adapter_path: Path,
    project: Path,
    root: Path,
    diagnostics: list[Diagnostic],
) -> set[str]:
    request = init.Request(
        target=project,
        kit_root=root,
        adapter=adapter_path.resolve(),
    )
    entries = adapter.get("docs_fragments", [])
    if not isinstance(entries, list):
        diagnostics.append(Diagnostic("adapter", "error", "docs_fragments", "ожидается список"))
        return set()
    invalid: set[int] = set()
    for index, entry in enumerate(entries):
        place = f"docs_fragments[{index}]"
        before = len(diagnostics)
        if not isinstance(entry, dict):
            diagnostics.append(Diagnostic("fragment", "error", place, "ожидается объект"))
            invalid.add(index)
            continue
        marker = entry.get("marker")
        target = entry.get("target")
        marker_ok = isinstance(marker, str) and init.FRAGMENT_MARKER.fullmatch(marker) is not None
        extra = set(entry) - {"target", "marker", "content", "content_file"}
        for key in sorted(extra):
            diagnostics.append(Diagnostic("fragment", "error", f"{place}.{key}", "неизвестное поле"))
        if not isinstance(target, str) or not target:
            diagnostics.append(Diagnostic("fragment", "error", f"{place}.target", "ожидается непустая строка"))
        if not marker_ok:
            diagnostics.append(Diagnostic("fragment", "error", f"{place}.marker", "ожидается adapter:<имя>"))
        try:
            content = init.fragment_content(entry, place, request)
        except init.InitError as error:
            diagnostics.append(Diagnostic("fragment", "error", place, str(error)))
            invalid.add(index)
            continue
        if marker_ok:
            checked = _fragment_conflict(
                entry, marker, content, documents, values, project, root, diagnostics
            )
            if not checked:
                try:
                    templates.validate_fragment(marker, content, place)
                except templates.TemplateError as error:
                    diagnostics.append(Diagnostic("fragment", "error", place, str(error)))
        if len(diagnostics) > before:
            invalid.add(index)

    filtered, missing = _without_missing_features(adapter, values, project, invalid)
    try:
        init.index_adapter_fragments(documents, values, filtered, request)
    except init.InitError as error:
        diagnostics.append(Diagnostic("fragment", "error", "docs_fragments", str(error)))
    return missing


def _fragment_conflict(
    entry: dict[str, Any],
    marker: str,
    content: str,
    documents: list[templates.Document],
    values: dict[str, Any],
    project: Path,
    root: Path,
    diagnostics: list[Diagnostic],
) -> bool:
    target = entry.get("target")
    if not isinstance(target, str):
        return False
    matches: list[tuple[templates.Document, str]] = []
    for document in documents:
        try:
            relative = templates.document_path(document, values)
        except templates.TemplateError as error:
            diagnostics.append(Diagnostic("fragment", "error", document.id, str(error)))
            return True
        if target in (document.id, relative, init.relative_to_docs(relative, values)):
            matches.append((document, relative))
    if len(matches) > 1:
        diagnostics.append(
            Diagnostic(
                "fragment",
                "error",
                "docs_fragments",
                f"цель {target} не указывает ровно один документ: {', '.join(sorted(path for _, path in matches))}",
            )
        )
        return True
    if matches:
        document, relative = matches[0]
        try:
            source = (root / document.template).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            diagnostics.append(
                Diagnostic("fragment", "error", document.template, f"шаблон не читается: {error}")
            )
            return True
        _fill_check(source, marker, content, document.template, diagnostics)
        return True
    docs_dir = values.get("docs", {}).get("dir")
    if not isinstance(docs_dir, str) or not docs_dir:
        return False
    prefix = docs_dir.rstrip("/") + "/"
    name = target[len(prefix):] if target.startswith(prefix) else target
    if not name.startswith("features/"):
        return False
    path = project / docs_dir / name
    if not path.is_file():
        return False
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        diagnostics.append(
            Diagnostic("fragment", "error", name, f"файл функции не читается: {error}")
        )
        return True
    _fill_check(source, marker, content, name, diagnostics)
    return True


def _fill_check(
    source: str,
    marker: str,
    content: str,
    where: str,
    diagnostics: list[Diagnostic],
) -> None:
    try:
        templates.fill_fragments(source, {marker: content}, where=where)
    except templates.TemplateError as error:
        diagnostics.append(Diagnostic("fragment", "error", where, str(error)))


def _without_missing_features(
    adapter: dict[str, Any],
    values: dict[str, Any],
    project: Path,
    skip: set[int] | None = None,
) -> tuple[dict[str, Any], set[str]]:
    docs_dir = values.get("docs", {}).get("dir")
    entries = adapter.get("docs_fragments", [])
    skipped = skip or set()
    if not isinstance(docs_dir, str) or not docs_dir:
        kept = [entry for index, entry in enumerate(entries) if index not in skipped]
        return dict(adapter, docs_fragments=kept), set()
    kept = []
    missing: set[str] = set()
    for index, entry in enumerate(entries):
        target = entry.get("target") if isinstance(entry, dict) else None
        if isinstance(target, str):
            name = target
            prefix = docs_dir.rstrip("/") + "/"
            if name.startswith(prefix):
                name = name[len(prefix):]
            if name.startswith("features/"):
                file_name = name.split("/", 1)[1]
                path = project / docs_dir / "features" / file_name
                if "/" not in file_name and not path.is_file():
                    missing.add(name)
                    continue
        if index not in skipped:
            kept.append(entry)
    return dict(adapter, docs_fragments=kept), missing


def _inputs_features(
    parsed: list[inputs.Input],
    values: dict[str, Any],
    project: Path,
    fragment_missing: set[str],
    diagnostics: list[Diagnostic],
) -> None:
    docs_dir = values.get("docs", {}).get("dir")
    missing: set[str] = set(fragment_missing)
    if isinstance(docs_dir, str) and docs_dir:
        for entry in parsed:
            if not entry.feature:
                continue
            path = project / docs_dir / entry.feature
            if not path.is_file():
                missing.add(entry.feature)
    if missing:
        names = ", ".join(sorted(missing))
        diagnostics.append(
            Diagnostic(
                "inputs",
                "warning",
                "features",
                f"файлов функций нет: {names}",
            )
        )


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = normalize(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "validate":
        args = args[1:]
    elif args and not args[0].startswith("-"):
        print("ожидается: ai-kit adapter validate --adapter <путь>", file=sys.stderr)
        return 2
    try:
        ensure_known(
            args,
            {"--adapter", "--profile", "--project", "--probes", "--json", "--help"},
            {"--adapter", "--profile", "--project"},
        )
    except UsageError as error:
        print(f"{error}\nИспользование: ai-kit adapter validate --adapter <путь>", file=sys.stderr)
        return 2
    if "--help" in args:
        print(__doc__)
        return 0
    adapter_path = _option(args, "--adapter")
    if not adapter_path:
        print("нужен --adapter <путь>", file=sys.stderr)
        return 2
    profile_arg = _option(args, "--profile")
    project_arg = _option(args, "--project")
    if profile_arg == "" or project_arg == "":
        print("после --profile и --project нужен путь", file=sys.stderr)
        return 2
    project = Path(project_arg or ".")
    report = validate(
        Path(adapter_path),
        Path(profile_arg) if profile_arg else None,
        project,
        run_probes="--probes" in args,
    )
    print(report.to_json() if "--json" in args else f"{report.to_table()}\n{report.summary()}")
    return report.exit_code


def _option(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    return args[index + 1] if index + 1 < len(args) else ""


if __name__ == "__main__":
    raise SystemExit(main())
