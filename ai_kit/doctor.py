"""Проверки готового набора: профиль, шаблоны, документы, реестр и гейты."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_kit import baseline, detect, gates, precommit, profile, rules, templates
from ai_kit.args import UsageError, ensure_known, normalize
from ai_kit.console import configure_console
from ai_kit.diagnostics import Diagnostic, Report
from ai_kit.paths import KitRootError, kit_root
from ai_kit.toml_io import TomlError, load as load_toml

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
FEATURE_LINK = re.compile(r"`([^`]+\.md)`")
EMPTY_ADAPTER = re.compile(r"<!-- adapter:([a-z0-9-]+):begin -->\s*<!-- adapter:\1:end -->")
EXTERNAL = ("http://", "https://", "mailto:", "tel:")


@dataclass(frozen=True)
class DoctorRequest:
    root: Path
    profile: Path
    adapter: Path | None = None
    kit_root: Path = Path(__file__).resolve().parents[1]
    gates: bool = False
    baseline: str = "baseline.toml"


def run(request: DoctorRequest) -> Report:
    diagnostics: list[Diagnostic] = []
    try:
        documents = templates.load_documents(request.kit_root / "documents.toml", request.kit_root)
    except (OSError, TomlError, templates.TemplateError) as error:
        return Report((Diagnostic("manifest", "error", "documents.toml", str(error)),))

    for error in templates.validate_documents(request.kit_root, documents):
        path, _, message = error.partition(": ")
        diagnostics.append(Diagnostic("template", "error", path, message))

    raw = _load_profile(request, diagnostics)
    adapter = _load_adapter(request, diagnostics)
    patterns, detect_errors = detect.load(adapter or {})
    diagnostics.extend(Diagnostic("adapter", "error", path, message) for path, message in detect_errors)
    if patterns and not detect.matches(request.root, patterns):
        diagnostics.append(
            Diagnostic("adapter", "warning", "detect", "адаптер не узнаёт проект: ни одна маска не найдена")
        )
    if raw is None:
        return Report(tuple(diagnostics))

    try:
        parsed = profile.parse(raw)
    except profile.ProfileError as error:
        diagnostics.extend(
            Diagnostic("profile", "error", path, message) for path, message in error.errors
        )
        return Report(tuple(diagnostics))

    diagnostics.extend(_document_diagnostics(documents, raw, request))
    diagnostics.extend(_features_diagnostics(documents, request, parsed))
    adapter_rules, rule_diagnostics = _adapter_rules(adapter)
    diagnostics.extend(rule_diagnostics)
    diagnostics.extend(_registry_diagnostics(documents, request, parsed, adapter_rules))
    diagnostics.extend(_baseline_diagnostics(request, _rule_ids(request, adapter_rules, parsed)))
    diagnostics.extend(_precommit_diagnostics(request, parsed))
    diagnostics.extend(_disabled_gaps(parsed))
    diagnostics.extend(_declared_gaps(adapter or {}))

    gate_list, gate_diagnostics = gates.load_gates(adapter or {})
    diagnostics.extend(gate_diagnostics)
    diagnostics.extend(_gate_coverage(gate_list, adapter_rules))
    if gate_list:
        if request.gates:
            baseline_path = _inside(request.root, request.baseline)
            if baseline_path is not None:
                results = gates.run_gates(gate_list, request.root, baseline_path)
                diagnostics.extend(results)
                diagnostics.extend(_stub_registry_diagnostics(results, gate_list, adapter_rules))
        else:
            diagnostics.append(
                Diagnostic("gate", "warning", "gates", "внешние гейты не запускались: нет --gates")
            )
    return Report(tuple(diagnostics))


def _stub_registry_diagnostics(
    results: list[Diagnostic],
    gate_list: list[gates.Gate],
    adapter_rules: list[rules.Rule],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    invariants = {gate.id: gate.invariant for gate in gate_list}
    labels = {rule.text: rule.label for rule in adapter_rules}
    for result in results:
        if result.severity != "gap" or "не реализован" not in result.message:
            continue
        label = labels.get(invariants.get(result.path, ""))
        if label in ("гейт", "гейт частичный"):
            diagnostics.append(
                Diagnostic(
                    "registry",
                    "error",
                    result.path,
                    f"гейт {result.path} помечен как «{label}», но заглушка не реализована",
                )
            )
    return diagnostics


def _load_profile(request: DoctorRequest, diagnostics: list[Diagnostic]) -> dict[str, Any] | None:
    try:
        return load_toml(request.profile)
    except (OSError, TomlError) as error:
        diagnostics.append(Diagnostic("profile", "error", request.profile.name, f"профиль не читается: {error}"))
        return None


def _load_adapter(request: DoctorRequest, diagnostics: list[Diagnostic]) -> dict[str, Any] | None:
    if request.adapter is None:
        return None
    try:
        return load_toml(request.adapter)
    except (OSError, TomlError) as error:
        diagnostics.append(Diagnostic("adapter", "error", request.adapter.name, f"адаптер не читается: {error}"))
        return None


def _adapter_rules(adapter: dict[str, Any] | None) -> tuple[list[rules.Rule], list[Diagnostic]]:
    if adapter is None:
        return [], []
    entries = adapter.get("rules", [])
    errors = rules.rule_errors(entries, "A", "rules")
    if errors:
        return [], [Diagnostic("registry", "error", path, message) for path, message in errors]
    return rules.build_rules(entries), []


def _document_diagnostics(
    documents: list[templates.Document],
    values: dict[str, Any],
    request: DoctorRequest,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for document in documents:
        relative = templates.document_path(document, values)
        path = _inside(request.root, relative)
        if path is None:
            diagnostics.append(Diagnostic("document", "error", relative, "путь выходит за каталог проекта"))
            continue
        if not path.is_file():
            diagnostics.append(Diagnostic("document", "error", relative, "документа нет по ожидаемому пути"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            diagnostics.append(Diagnostic("document", "error", relative, f"документ не читается: {error}"))
            continue
        diagnostics.extend(_text_diagnostics(text, relative))
        diagnostics.extend(_link_diagnostics(text, path, relative, request.root))
    return diagnostics


def _text_diagnostics(text: str, relative: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if "{{" in text:
        diagnostics.append(Diagnostic("placeholder", "error", relative, "в результате остались подстановки шаблона"))
    headings = [line for line in text.splitlines() if re.match(r"^#\s+", line)]
    if len(headings) != 1:
        diagnostics.append(
            Diagnostic("document", "error", relative, f"ожидается один заголовок первого уровня, найдено {len(headings)}")
        )
    for error in templates.validate_markers(text):
        diagnostics.append(Diagnostic("marker", "error", relative, error))
    for empty in templates.empty_sections(text):
        diagnostics.append(Diagnostic("section", "warning", relative, f"пустой раздел {empty}"))
    if "/templates/" not in relative:
        for match in EMPTY_ADAPTER.finditer(text):
            diagnostics.append(
                Diagnostic("adapter", "warning", relative, f"фрагмент adapter:{match.group(1)} не заполнен")
            )
    return diagnostics


def _link_diagnostics(text: str, path: Path, relative: str, root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for match in LINK.finditer(text):
        target = match.group(1)
        if target.startswith(EXTERNAL) or target.startswith("#"):
            continue
        clean = target.split("#", 1)[0]
        if not clean:
            continue
        if clean.startswith("/"):
            diagnostics.append(Diagnostic("link", "error", relative, f"абсолютная ссылка: {target}"))
            continue
        resolved = (path.parent / clean).resolve()
        if _inside(root, resolved.as_posix()) is None:
            diagnostics.append(Diagnostic("link", "error", relative, f"ссылка выходит за проект: {target}"))
            continue
        if not resolved.exists():
            diagnostics.append(Diagnostic("link", "error", relative, f"цель ссылки не найдена: {target}"))
    return diagnostics


def _registry_diagnostics(
    documents: list[templates.Document],
    request: DoctorRequest,
    parsed: profile.Profile,
    adapter_rules: list[rules.Rule],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    try:
        canon = rules.load_canon(request.kit_root / "canon" / "registry.md")
    except (OSError, ValueError) as error:
        return [Diagnostic("registry", "error", "canon/registry.md", f"канон не прочитан: {error}")]

    registry_text = _document_text(documents, request, parsed, "registry")
    if registry_text is None:
        diagnostics.append(Diagnostic("registry", "error", "registry", "документа реестра нет"))
    else:
        for row in canon + adapter_rules + parsed.rules:
            if f"| {row.id} |" not in registry_text:
                diagnostics.append(
                    Diagnostic("registry", "error", "registry", f"строка {row.id} не попала в реестр")
                )

    debt_text = _document_text(documents, request, parsed, "tech-debt")
    if parsed.gates and debt_text is not None:
        for gate in parsed.gates:
            if f"| {gate.id} |" not in debt_text:
                diagnostics.append(
                    Diagnostic("registry", "error", "tech-debt", f"гейт {gate.id} не попал в техдолг")
                )
    return diagnostics


def _document_text(
    documents: list[templates.Document],
    request: DoctorRequest,
    parsed: profile.Profile,
    document_id: str,
) -> str | None:
    document = next((item for item in documents if item.id == document_id), None)
    if document is None:
        return None
    values = {
        "docs": {"dir": parsed.docs.dir},
        "project": {"name": parsed.project.name},
    }
    relative = templates.document_path(document, values)
    path = _inside(request.root, relative)
    if path is None or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _rule_ids(
    request: DoctorRequest,
    adapter_rules: list[rules.Rule],
    parsed: profile.Profile,
) -> set[str]:
    ids = {rule.id for rule in adapter_rules} | {rule.id for rule in parsed.rules}
    try:
        ids.update(rule.id for rule in rules.load_canon(request.kit_root / "canon" / "registry.md"))
    except (OSError, ValueError):
        pass
    return ids


def _baseline_diagnostics(request: DoctorRequest, rule_ids: set[str]) -> list[Diagnostic]:
    path = request.root / request.baseline
    if _inside(request.root, request.baseline) is None:
        return [Diagnostic("baseline", "error", request.baseline, "путь выходит за каталог проекта")]
    if not path.is_file():
        return []
    try:
        data = load_toml(path)
    except (OSError, TomlError) as error:
        return [Diagnostic("baseline", "error", request.baseline, f"baseline не читается: {error}")]
    return [
        Diagnostic("baseline", "error", f"{request.baseline}:{place}" if place else request.baseline, message)
        for place, message in baseline.violation_errors(data, rule_ids)
    ]


def _precommit_diagnostics(request: DoctorRequest, parsed: profile.Profile) -> list[Diagnostic]:
    if not parsed.automation.pre_commit:
        return []
    diagnostics: list[Diagnostic] = []
    for relative in (precommit.PRE_COMMIT, precommit.HOOK):
        if not (request.root / relative).is_file():
            diagnostics.append(
                Diagnostic("precommit", "error", relative, "нет артефакта проверки перед коммитом")
            )
    return diagnostics


def _features_diagnostics(
    documents: list[templates.Document],
    request: DoctorRequest,
    parsed: profile.Profile,
) -> list[Diagnostic]:
    folder = _inside(request.root, f"{parsed.docs.dir}/features")
    if folder is None or not folder.is_dir():
        return []
    text = _document_text(documents, request, parsed, "features-index")
    if text is None:
        return []
    diagnostics: list[Diagnostic] = []
    listed: set[str] = set()
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        for match in FEATURE_LINK.finditer(line):
            relative = match.group(1)
            listed.add(relative)
            target = _inside(request.root, f"{parsed.docs.dir}/{relative}")
            if target is None or not target.is_file():
                diagnostics.append(
                    Diagnostic("features", "error", "FEATURES.md", f"файла функции нет: {relative}")
                )
    for path in sorted(folder.glob("*.md")):
        relative = f"features/{path.name}"
        if relative not in listed:
            diagnostics.append(
                Diagnostic("features", "error", "FEATURES.md", f"функция не внесена в карту: {relative}")
            )
    return diagnostics


def _disabled_gaps(parsed: profile.Profile) -> list[Diagnostic]:
    return [
        Diagnostic("gate", "gap", gate.id, f"гейт отключён: {gate.reason}")
        for gate in parsed.gates
    ]


def _declared_gaps(adapter: dict[str, Any]) -> list[Diagnostic]:
    entries = adapter.get("gaps", [])
    if not isinstance(entries, list):
        return [Diagnostic("adapter", "error", "gaps", "ожидается список пробелов")]
    diagnostics: list[Diagnostic] = []
    for index, entry in enumerate(entries):
        place = f"gaps[{index}]"
        if not isinstance(entry, dict):
            diagnostics.append(Diagnostic("adapter", "error", place, "ожидается объект"))
            continue
        extra = set(entry) - {"id", "reason", "owner", "trigger"}
        for key in sorted(extra):
            diagnostics.append(Diagnostic("adapter", "error", f"{place}.{key}", "неизвестное поле"))
        for key in ("id", "reason", "owner", "trigger"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                diagnostics.append(Diagnostic("adapter", "error", f"{place}.{key}", "ожидается непустая строка"))
        if all(isinstance(entry.get(key), str) and entry.get(key).strip() for key in ("id", "reason", "owner", "trigger")):
            diagnostics.append(Diagnostic("gate", "gap", entry["id"], f"заявленный пробел: {entry['reason']}"))
    return diagnostics


def _gate_coverage(gate_list: list[gates.Gate], adapter_rules: list[rules.Rule]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    texts = {rule.text for rule in adapter_rules}
    for gate in gate_list:
        if gate.invariant not in texts:
            diagnostics.append(
                Diagnostic(
                    "registry",
                    "error",
                    gate.id,
                    f"у гейта {gate.id} нет строки реестра с инвариантом «{gate.invariant}»",
                )
            )
    return diagnostics


def _inside(root: Path, relative: str) -> Path | None:
    base = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


def _option(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    index = args.index(name)
    return args[index + 1] if index + 1 < len(args) else ""


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = normalize(sys.argv[1:] if argv is None else argv)
    try:
        ensure_known(args, {"--profile", "--adapter", "--gates", "--json", "--baseline", "--help"})
    except UsageError as error:
        print(f"{error}\nИспользование: python -m ai_kit.doctor --help", file=sys.stderr)
        return 2
    if "--help" in args:
        print(__doc__)
        return 0

    profile_path = _option(args, "--profile")
    adapter_path = _option(args, "--adapter")
    if profile_path is None or profile_path == "" or adapter_path == "":
        print(
            "Использование: python -m ai_kit.doctor --profile <путь> [--adapter <путь>] [--gates] [--baseline <путь>] [--json]",
            file=sys.stderr,
        )
        return 2

    try:
        root = kit_root()
    except KitRootError as error:
        print(error, file=sys.stderr)
        return 2

    request = DoctorRequest(
        root=Path.cwd(),
        profile=Path(profile_path),
        adapter=Path(adapter_path) if adapter_path else None,
        kit_root=root,
        gates="--gates" in args,
        baseline=_option(args, "--baseline") or "baseline.toml",
    )
    report = run(request)
    if "--json" in args:
        print(report.to_json())
    else:
        print(report.to_table())
        print(report.summary())
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
