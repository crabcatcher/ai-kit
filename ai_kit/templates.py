"""Шаблоны документов: разбор, проверка, рендеринг, разрешение ссылок и команд."""

from __future__ import annotations

import posixpath
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_kit import schema
from ai_kit.console import configure_console

COMMAND_ROLES = ("test", "unit", "e2e", "lint", "build", "migrate")
NO_COMMAND = "гейтов нет"
PATH_PLACEHOLDERS = {"docs.dir"}

BLOCK_TAG = re.compile(r"\{\{\s*(#each\s+[A-Za-z0-9_.]+|#if\s+[A-Za-z0-9_.]+|/each|/if)\s*\}\}")
TAG = re.compile(r"\{\{\s*(.+?)\s*\}\}")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
MARKER = re.compile(r"^<!--\s*(adapter|generated):([a-z0-9][a-z0-9-]*):(begin|end)\s*-->$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
SLOT = re.compile(r"^[A-Za-z0-9_.]+$")
FRAGMENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_MISSING = object()


class TemplateError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Document:
    id: str
    template: str
    path: str
    title: str


class Text:
    def __init__(self, content: str) -> None:
        self.content = content


class Each:
    def __init__(self, path: str) -> None:
        self.path = path
        self.children: list[Any] = []


class If:
    def __init__(self, path: str) -> None:
        self.path = path
        self.children: list[Any] = []


@dataclass(frozen=True)
class RenderContext:
    document: Document
    documents: list[Document]
    root: Path
    values: dict[str, Any]
    commands: dict[str, str]
    fragments: dict[str, str]


def load_documents(manifest: Path, root: Path) -> list[Document]:
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    errors: list[str] = []
    entries = data.get("document")
    if not isinstance(entries, list) or not entries:
        raise TemplateError(["в манифесте нет ни одного документа"])

    documents: list[Document] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, entry in enumerate(entries):
        place = f"document[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{place}: ожидается объект")
            continue
        extra = set(entry) - {"id", "template", "path", "title"}
        for key in sorted(extra):
            errors.append(f"{place}.{key}: неизвестное поле")
        values = {}
        for key in ("id", "template", "path", "title"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{place}.{key}: ожидается непустая строка")
            else:
                values[key] = value
        if len(values) != 4:
            continue
        if values["id"] in seen_ids:
            errors.append(f"{place}.id: идентификатор повторяется")
        seen_ids.add(values["id"])
        if values["path"] in seen_paths:
            errors.append(f"{place}.path: путь повторяется")
        seen_paths.add(values["path"])
        if values["path"].startswith(("/", "\\")) or ".." in values["path"].split("/"):
            errors.append(f"{place}.path: путь должен быть относительным и без перехода вверх")
        for match in PLACEHOLDER.finditer(values["path"]):
            if match.group(1) not in PATH_PLACEHOLDERS:
                errors.append(f"{place}.path: неизвестная подстановка {{{match.group(1)}}}")
        template = root / values["template"]
        if not template.is_file():
            errors.append(f"{place}.template: файла нет: {values['template']}")
        documents.append(Document(**values))

    if errors:
        raise TemplateError(errors)
    return documents


def validate_documents(root: Path, documents: list[Document]) -> list[str]:
    document_ids = {document.id for document in documents}
    errors: list[str] = []
    for document in documents:
        source = (root / document.template).read_text(encoding="utf-8")
        for error in validate_template(source, document_ids=document_ids):
            errors.append(f"{document.template}: {error}")
    return errors


def validate_template(source: str, *, document_ids: set[str]) -> list[str]:
    source = _strip_notes(source)
    try:
        nodes = _parse(source)
    except TemplateError as error:
        return list(error.errors)

    validator = _Validator(document_ids)
    validator.nodes(nodes, None)
    _validate_markers(source, validator.errors)

    first_level = [line for line in source.splitlines() if re.match(r"^#\s+", line)]
    if len(first_level) != 1:
        validator.errors.append(f"ожидается ровно один заголовок первого уровня, найдено {len(first_level)}")
    return validator.errors


def _parse(source: str) -> list[Any]:
    root: list[Any] = []
    stack: list[tuple[list[Any], str]] = [(root, "")]
    position = 0
    for match in BLOCK_TAG.finditer(source):
        text = source[position:match.start()]
        if text:
            stack[-1][0].append(Text(text))
        tag = match.group(1)
        if tag.startswith("#each "):
            node = Each(tag[len("#each "):].strip())
            stack[-1][0].append(node)
            stack.append((node.children, "each"))
        elif tag.startswith("#if "):
            node = If(tag[len("#if "):].strip())
            stack[-1][0].append(node)
            stack.append((node.children, "if"))
        else:
            expected = tag[1:]
            if len(stack) == 1:
                raise TemplateError([f"закрывающий тег {tag} без открывающего"])
            if stack[-1][1] != expected:
                raise TemplateError([f"закрывающий тег {tag} не совпадает с открытым {stack[-1][1]}"])
            stack.pop()
        position = match.end()

    tail = source[position:]
    if tail:
        stack[-1][0].append(Text(tail))
    if len(stack) > 1:
        raise TemplateError([f"блок {stack[-1][1]} не закрыт"])
    return root


class _Validator:
    def __init__(self, document_ids: set[str]) -> None:
        self.document_ids = document_ids
        self.errors: list[str] = []

    def nodes(self, nodes: list[Any], scope_schema: dict[str, Any] | None) -> None:
        for node in nodes:
            if isinstance(node, Text):
                for match in TAG.finditer(node.content):
                    self.tag(match.group(1).strip(), scope_schema)
            elif isinstance(node, Each):
                node_schema = self.resolve(node.path, scope_schema)
                item_schema = None
                if node_schema is not None:
                    if node_schema.get("type") != "array":
                        self.errors.append(f"{node.path}: ожидается список")
                    else:
                        item_schema = node_schema.get("items")
                self.nodes(node.children, item_schema)
            elif isinstance(node, If):
                self.resolve(node.path, scope_schema)
                self.nodes(node.children, scope_schema)

    def tag(self, tag: str, scope_schema: dict[str, Any] | None) -> None:
        if tag == "this":
            if scope_schema is None:
                self.errors.append("this допустим только внутри блока each")
            return
        if tag.startswith("cmd:"):
            role = tag[len("cmd:"):]
            if role not in COMMAND_ROLES:
                self.errors.append(f"cmd:{role}: неизвестная роль, ожидается {', '.join(COMMAND_ROLES)}")
            return
        if tag.startswith("link:"):
            target = tag[len("link:"):]
            if target not in self.document_ids:
                self.errors.append(f"link:{target}: документа нет в манифесте")
            return
        if SLOT.fullmatch(tag) is None:
            self.errors.append(f"{{{{ {tag} }}}}: неизвестная конструкция")
            return
        self.resolve(tag, scope_schema)

    def resolve(self, path: str, scope_schema: dict[str, Any] | None) -> dict[str, Any] | None:
        node = None
        if scope_schema is not None:
            node = schema.resolve(path, scope_schema)
        if node is None:
            node = schema.resolve(path)
        if node is None:
            self.errors.append(f"{path}: поля нет в схеме профиля")
        return node


def _strip_notes(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        return text if MARKER.match(text.strip()) else ""

    return COMMENT.sub(replace, source)


def _validate_markers(source: str, errors: list[str]) -> None:
    stack: list[str] = []
    seen: set[str] = set()
    for line in source.splitlines():
        match = MARKER.match(line.strip())
        if match is None:
            continue
        kind, name, action = match.groups()
        key = f"{kind}:{name}"
        if action == "begin":
            if key in seen:
                errors.append(f"маркер {key} встречается больше одного раза")
            stack.append(key)
            seen.add(key)
            continue
        if not stack or stack[-1] != key:
            errors.append(f"маркер {key}: закрытие без открытия")
            continue
        stack.pop()
    for key in stack:
        errors.append(f"маркер {key} не закрыт")


def validate_markers(source: str) -> list[str]:
    errors: list[str] = []
    _validate_markers(_strip_notes(source), errors)
    return errors


def empty_sections(source: str) -> list[str]:
    lines = source.splitlines()
    headings = [
        (index, len(match.group(1)), match.group(2))
        for index, line in enumerate(lines)
        if (match := HEADING.match(line))
    ]
    result: list[str] = []
    for position, (start, level, title) in enumerate(headings):
        if level == 1:
            continue
        end = len(lines)
        for later_start, later_level, _ in headings[position + 1:]:
            if later_level <= level:
                end = later_start
                break
        if _body_is_empty(lines[start + 1:end]):
            result.append(f"{start + 1}: {title}")
    return result


def marker_names(source: str, kind: str) -> list[str]:
    if kind not in ("adapter", "generated"):
        raise TemplateError([f"{kind}: ожидается adapter или generated"])
    names: list[str] = []
    for line in _strip_notes(source).splitlines():
        match = MARKER.match(line.strip())
        if match is None or match.group(3) != "begin" or match.group(1) != kind:
            continue
        name = match.group(2)
        if name not in names:
            names.append(name)
    return names


def render(source: str, context: RenderContext) -> str:
    source = _strip_notes(source)
    errors = validate_template(source, document_ids={document.id for document in context.documents})
    if errors:
        raise TemplateError(errors)

    nodes = _parse(source)
    text = _render_nodes(nodes, context.values, context.values, context)
    text = _insert_fragments(text, context)
    return _normalize(_strip_empty_sections(text.splitlines()))


def render_document(context: RenderContext) -> str:
    source = (context.root / context.document.template).read_text(encoding="utf-8")
    return render(source, context)


def _render_nodes(nodes: list[Any], scope: Any, root: dict[str, Any], context: RenderContext) -> str:
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, Text):
            parts.append(_substitute(node.content, scope, root, context))
        elif isinstance(node, Each):
            items = _lookup_optional(node.path, scope, root)
            if items is _MISSING or items is None:
                items = []
            if not isinstance(items, list):
                raise TemplateError([f"{node.path}: ожидается список"])
            for item in items:
                parts.append(_render_nodes(node.children, item, root, context))
        elif isinstance(node, If):
            value = _lookup_optional(node.path, scope, root)
            if value is not _MISSING and _truthy(value):
                parts.append(_render_nodes(node.children, scope, root, context))
    return "".join(parts)


def _substitute(text: str, scope: Any, root: dict[str, Any], context: RenderContext) -> str:
    def replace(match: re.Match[str]) -> str:
        tag = match.group(1).strip()
        if tag == "this":
            if isinstance(scope, (dict, list)):
                raise TemplateError(["this допустим только для элемента-значения"])
            return _format(scope)
        if tag.startswith("cmd:"):
            return context.commands.get(tag[len("cmd:"):], NO_COMMAND)
        if tag.startswith("link:"):
            return _link(tag[len("link:"):], context)
        return _format(_lookup(tag, scope, root))

    return TAG.sub(replace, text)


def _lookup(path: str, scope: Any, root: dict[str, Any]) -> Any:
    value = _lookup_optional(path, scope, root)
    if value is _MISSING:
        raise TemplateError([f"{path}: нет значения"])
    return value


def _lookup_optional(path: str, scope: Any, root: dict[str, Any]) -> Any:
    if isinstance(scope, dict):
        value = _walk_value(scope, path)
        if value is not _MISSING:
            return value
    return _walk_value(root, path)


def _walk_value(values: dict[str, Any], path: str) -> Any:
    node: Any = values
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _format(value: Any) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (str, int)):
        return str(value)
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, (dict, list)):
                raise TemplateError(["элемент списка — объект: нужен блок each"])
            items.append(str(item))
        return ", ".join(items)
    raise TemplateError([f"значение {value!r} нельзя подставить"])


def _link(target_id: str, context: RenderContext) -> str:
    targets = {document.id: document for document in context.documents}
    current = document_path(context.document, context.values)
    target = document_path(targets[target_id], context.values)
    start = posixpath.dirname(current) or "."
    relative = posixpath.relpath(target, start)
    return relative if relative.startswith(".") else f"./{relative}"


def document_path(document: Document, values: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        node: Any = values
        for part in name.split("."):
            if not isinstance(node, dict) or part not in node:
                raise TemplateError([f"{document.path}: нет значения для {{{name}}}"])
            node = node[part]
        if not isinstance(node, str) or not node:
            raise TemplateError([f"{document.path}: {{{name}}} должен быть строкой"])
        return node

    return PLACEHOLDER.sub(replace, document.path)


def _truthy(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (str, list, dict, int)):
        return bool(value)
    return True


def _insert_fragments(text: str, context: RenderContext) -> str:
    for key, fragment in context.fragments.items():
        kind, _, name = key.partition(":")
        if kind not in ("adapter", "generated") or not FRAGMENT_NAME.fullmatch(name):
            raise TemplateError([f"фрагмент {key}: ожидается adapter:<имя> или generated:<имя>"])
        _validate_fragment(key, fragment)
        pattern = re.compile(
            rf"(<!-- {kind}:{re.escape(name)}:begin -->)\n?(.*?)(<!-- {kind}:{re.escape(name)}:end -->)",
            re.DOTALL,
        )
        if pattern.search(text) is None:
            raise TemplateError([f"фрагмент {key}: маркера нет в шаблоне"])
        body = fragment.strip()
        begin = f"<!-- {kind}:{name}:begin -->"
        end = f"<!-- {kind}:{name}:end -->"
        replacement = f"{begin}\n{body}\n{end}" if body else f"{begin}\n{end}"
        text = pattern.sub(lambda _: replacement, text, count=1)
    return text


def _validate_fragment(key: str, fragment: str) -> None:
    errors: list[str] = []
    for line in fragment.splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,6}\s", stripped):
            errors.append(f"{key}: заголовок внутри фрагмента — заголовки принадлежат шаблону")
        if MARKER.match(stripped):
            errors.append(f"{key}: маркер внутри фрагмента")
        if "{{" in stripped:
            errors.append(f"{key}: подстановки внутри фрагмента не выполняются")
    if errors:
        raise TemplateError(errors)


def _strip_empty_sections(lines: list[str]) -> list[str]:
    while True:
        headings = [(index, len(match.group(1))) for index, line in enumerate(lines) if (match := HEADING.match(line))]
        removed = False
        for position in range(len(headings) - 1, -1, -1):
            start, level = headings[position]
            if level == 1:
                continue
            end = len(lines)
            for later_start, later_level in headings[position + 1:]:
                if later_level <= level:
                    end = later_start
                    break
            if _body_is_empty(lines[start + 1:end]):
                del lines[start:end]
                removed = True
                break
        if not removed:
            return lines


def _body_is_empty(body: list[str]) -> bool:
    content = []
    for line in body:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        content.append(stripped)
    if not content:
        return True
    if len(content) == 1 and re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", content[0]):
        return True
    if len(content) == 2 and "|" in content[0] and _is_table_separator(content[1]):
        return True
    return False


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells)


def _normalize(lines: list[str]) -> str:
    cleaned = [line.rstrip() for line in lines]
    output: list[str] = []
    blank = False
    for line in cleaned:
        if line:
            output.append(line)
            blank = False
        elif not blank:
            output.append("")
            blank = True
    while output and output[0] == "":
        output.pop(0)
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output) + "\n"


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = sys.argv[1:] if argv is None else argv
    root = Path.cwd()

    try:
        documents = load_documents(root / "documents.toml", root)
    except (OSError, tomllib.TOMLDecodeError, TemplateError) as error:
        print(f"не удалось прочитать манифест: {error}", file=sys.stderr)
        return 2

    if "--check" in args:
        errors = validate_documents(root, documents)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print(f"Шаблоны валидны: {len(documents)}.")
        return 0

    if "--render" in args:
        index = args.index("--render")
        if index + 1 >= len(args):
            print("--render требует идентификатор документа", file=sys.stderr)
            return 2
        document_id = args[index + 1]
        profile_index = args.index("--profile") if "--profile" in args else -1
        if profile_index < 0 or profile_index + 1 >= len(args):
            print("--render требует --profile <путь>", file=sys.stderr)
            return 2
        document = next((item for item in documents if item.id == document_id), None)
        if document is None:
            print(f"документа {document_id} нет в манифесте", file=sys.stderr)
            return 2
        try:
            with Path(args[profile_index + 1]).open("rb") as handle:
                values = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as error:
            print(f"не удалось прочитать профиль: {error}", file=sys.stderr)
            return 2
        commands = values.get("tests", {}).get("commands", {})
        context = RenderContext(
            document=document,
            documents=documents,
            root=root,
            values=values,
            commands={str(role): str(command) for role, command in commands.items()},
            fragments={},
        )
        try:
            print(render_document(context), end="")
        except TemplateError as error:
            print(error, file=sys.stderr)
            return 1
        return 0

    print("Использование: python -m ai_kit.templates --check | --render <id> --profile <путь>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
