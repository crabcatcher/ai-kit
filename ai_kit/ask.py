"""Интерактивный ввод ответов: вопросы по слотам и подтверждение записи."""

from __future__ import annotations

import json
from typing import Any, Callable

from ai_kit import interview, schema
from ai_kit.templates import COMMAND_ROLES

Ask = Callable[[str], str]
Tell = Callable[[str], None]
YES = {"y", "yes", "д", "да", "1", "true"}
NO = {"n", "no", "н", "нет", "0", "false"}


class AskAborted(Exception):
    pass


def collect(
    questions: list[interview.Question],
    values: dict[str, Any],
    *,
    ask: Ask | None = None,
    tell: Tell | None = None,
) -> list[interview.Answer]:
    return Prompter(ask or input, tell or print).collect(questions, values)


def confirm(*, ask: Ask | None = None) -> bool:
    return Prompter(ask or input, print).confirm()


class Prompter:
    def __init__(self, ask: Ask, tell: Tell) -> None:
        self.ask = ask
        self.tell = tell

    def collect(
        self,
        questions: list[interview.Question],
        values: dict[str, Any],
    ) -> list[interview.Answer]:
        answers: list[interview.Answer] = []
        for number, question in enumerate(questions, start=1):
            self.tell("")
            self.tell(f"{number}. {question.text}")
            self.tell(f"   почему: {question.why}")
            for slot in question.slots:
                if interview.is_known(values, slot):
                    continue
                answers.append(self.slot(slot))
        return answers

    def confirm(self) -> bool:
        return self.read("Записать файлы? [y/N]: ").lower() in YES

    def slot(self, path: str) -> interview.Answer:
        node = schema.resolve(path)
        if node is None:
            self.tell(f"  ! {path}: поля нет в схеме, пропущено")
            return interview.Answer(path, status="unknown")
        if schema.is_evidence(node):
            return self.evidence(path)
        kind = node.get("type")
        if kind == "string":
            return self.string(path, node)
        if kind == "boolean":
            return self.boolean(path)
        if kind == "array":
            return self.array(path, node)
        if kind == "object":
            return self.object(path, node)
        if kind in ("integer", "number"):
            return self.number(path, node, kind)
        self.tell(f"  ! {path}: тип {kind} не поддерживается, пропущено")
        return interview.Answer(path, status="unknown")

    def evidence(self, path: str) -> interview.Answer:
        value = self.read(f"  {path} [имя факта, Enter — неизвестно]: ")
        if not value:
            return interview.Answer(path, status="unknown")
        return interview.Answer(path, name=value)

    def string(self, path: str, node: dict[str, Any]) -> interview.Answer:
        value = self.read(f"  {path} [строка, Enter — неизвестно]: ")
        if not value:
            return interview.Answer(path, status="unknown")
        return self.checked(path, node, value)

    def boolean(self, path: str) -> interview.Answer:
        while True:
            value = self.read(f"  {path} [y/n, Enter — неизвестно]: ").lower()
            if not value:
                return interview.Answer(path, status="unknown")
            if value in YES:
                return interview.Answer(path, value=True)
            if value in NO:
                return interview.Answer(path, value=False)
            self.tell("  ! ожидается y или n")

    def number(self, path: str, node: dict[str, Any], kind: str) -> interview.Answer:
        value = self.read(f"  {path} [{kind}, Enter — неизвестно]: ")
        if not value:
            return interview.Answer(path, status="unknown")
        try:
            parsed: Any = int(value) if kind == "integer" else float(value)
        except ValueError:
            self.tell("  ! ожидается число")
            return self.number(path, node, kind)
        return self.checked(path, node, parsed)

    def array(self, path: str, node: dict[str, Any]) -> interview.Answer:
        item = node.get("items", {}).get("type")
        if item == "string":
            value = self.read(f"  {path} [значения через запятую, Enter — неизвестно]: ")
            if not value:
                return interview.Answer(path, status="unknown")
            parsed = self.list_value(value)
            return self.checked(path, node, parsed)

        value = self.read(f"  {path} [JSON-список, Enter — неизвестно]: ")
        if not value:
            return interview.Answer(path, status="unknown")
        return self.json_value(path, node, value)

    def object(self, path: str, node: dict[str, Any]) -> interview.Answer:
        additional = node.get("additionalProperties")
        if isinstance(additional, dict) and additional.get("type") == "string":
            values: dict[str, str] = {}
            for role in COMMAND_ROLES:
                value = self.read(f"  {path}.{role} [строка, Enter — пропустить]: ")
                if value:
                    values[role] = value
            if not values:
                return interview.Answer(path, status="unknown")
            return self.checked(path, node, values)

        value = self.read(f"  {path} [JSON-объект, Enter — неизвестно]: ")
        if not value:
            return interview.Answer(path, status="unknown")
        return self.json_value(path, node, value)

    def json_value(self, path: str, node: dict[str, Any], raw: str) -> interview.Answer:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            self.tell(f"  ! JSON не разбирается: {error}")
            return self.slot(path)
        return self.checked(path, node, value)

    def list_value(self, raw: str) -> Any:
        if raw.startswith("["):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in raw.split(",") if part.strip()]

    def checked(self, path: str, node: dict[str, Any], value: Any) -> interview.Answer:
        try:
            interview.validate_value(node, value, path)
        except interview.InterviewError as error:
            message = "; ".join(message for _, message in error.errors)
            self.tell(f"  ! {message}")
            return self.slot(path)
        return interview.Answer(path, value=value)

    def read(self, prompt: str) -> str:
        try:
            return self.ask(prompt).strip()
        except EOFError as error:
            raise AskAborted("ввод прерван") from error
