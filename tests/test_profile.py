import copy
import dataclasses
import json
import tomllib
import unittest
from pathlib import Path

from ai_kit.profile import (
    Automation,
    DisabledGate,
    Docs,
    Evidence,
    Layer,
    Layout,
    Process,
    Profile,
    ProfileError,
    Project,
    Security,
    Stack,
    Tests,
    Unknown,
    parse,
    print_schema,
)
from ai_kit.inputs import Input
from ai_kit.rules import Rule

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "profiles" / "example.toml"


def example_data() -> dict:
    with EXAMPLE.open("rb") as handle:
        return tomllib.load(handle)


class ProfileTests(unittest.TestCase):
    def assert_error(self, data: dict, path: str) -> None:
        with self.assertRaises(ProfileError) as context:
            parse(data)
        paths = [error_path for error_path, _ in context.exception.errors]
        self.assertIn(path, paths)

    def test_example_is_valid(self) -> None:
        profile = parse(example_data())
        self.assertEqual(profile.project.name, "example")
        self.assertEqual(len(profile.layout.layers), 3)
        self.assertEqual(profile.rules[0].id, "P1")
        self.assertEqual(profile.gates[0].trigger, "второй разработчик")

    def test_rule_without_executor_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["rules"][0]["label"] = "гейт"
        self.assert_error(data, "rules[0].executor")

    def test_rule_without_reason_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["rules"][0]["label"] = "нет гейта"
        self.assert_error(data, "rules[0].reason")

    def test_rule_number_outside_space_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["rules"][0]["id"] = "K9"
        self.assert_error(data, "rules[0].id")

    def test_version_from_answer_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["stack"]["runtime"][0]["provenance"] = "answer"
        self.assert_error(data, "stack.runtime[0].version")

    def test_version_without_evidence_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        del data["stack"]["runtime"][0]["evidence"]
        self.assert_error(data, "stack.runtime[0].evidence")

    def test_inputs_accepted_and_canonicalized(self) -> None:
        data = copy.deepcopy(example_data())
        data["inputs"] = [
            {"id": "health", "kind": "http", "locator": "GET /health", "feature": "health.md"}
        ]
        profile = parse(data)
        self.assertEqual(profile.inputs[0].feature, "features/health.md")

    def test_input_without_feature_accepted(self) -> None:
        data = copy.deepcopy(example_data())
        data["inputs"] = [{"id": "health", "kind": "http", "locator": "GET /health"}]
        self.assertIsNone(parse(data).inputs[0].feature)

    def test_input_bad_id_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["inputs"] = [{"id": "Health", "kind": "http", "locator": "GET /health"}]
        self.assert_error(data, "inputs[0].id")

    def test_not_applicable_field_accepted(self) -> None:
        data = copy.deepcopy(example_data())
        data["not_applicable"] = [{"field": "automation.clients", "reason": "клиентов агента нет"}]
        parse(data)

    def test_not_applicable_required_field_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["not_applicable"] = [{"field": "project.name", "reason": "нет"}]
        self.assert_error(data, "not_applicable[0].field")

    def test_not_applicable_with_value_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["not_applicable"] = [{"field": "automation.ci", "reason": "нет"}]
        self.assert_error(data, "not_applicable[0].field")

    def test_not_applicable_unknown_field_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["not_applicable"] = [{"field": "project.slogan", "reason": "нет"}]
        self.assert_error(data, "not_applicable[0].field")

    def test_not_applicable_duplicate_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["not_applicable"] = [
            {"field": "automation.clients", "reason": "нет"},
            {"field": "automation.clients", "reason": "повтор"},
        ]
        self.assert_error(data, "not_applicable[1].field")

    def test_not_applicable_inputs_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["not_applicable"] = [{"field": "inputs", "reason": "входов нет"}]
        self.assert_error(data, "not_applicable[0].field")

    def test_pre_commit_is_required(self) -> None:
        data = copy.deepcopy(example_data())
        del data["automation"]["pre_commit"]
        self.assert_error(data, "automation.pre_commit")

    def test_unknown_key_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["project"]["slogan"] = "x"
        self.assert_error(data, "project.slogan")

    def test_disabled_gate_without_trigger_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        del data["gates"]["disabled"][0]["trigger"]
        self.assert_error(data, "gates.disabled[0].trigger")

    def test_missing_purpose_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        del data["project"]["purpose"]
        self.assert_error(data, "project.purpose")

    def test_missing_boundaries_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["project"]["boundaries"] = []
        self.assert_error(data, "project.boundaries")

    def test_duplicate_layer_rejected(self) -> None:
        data = copy.deepcopy(example_data())
        data["layout"]["layers"][1]["name"] = "domain"
        self.assert_error(data, "layout.layers[1].name")

    def test_schema_covers_dataclass_fields(self) -> None:
        schema = json.loads(print_schema())
        properties = schema["properties"]
        checks = [
            (Profile, schema, {"adapter_id": "adapter"}),
            (Project, properties["project"], {}),
            (Docs, properties["docs"], {}),
            (Stack, properties["stack"], {}),
            (Evidence, properties["stack"]["properties"]["runtime"]["items"], {}),
            (Layout, properties["layout"], {}),
            (Layer, properties["layout"]["properties"]["layers"]["items"], {}),
            (Tests, properties["tests"], {}),
            (Process, properties["process"], {}),
            (Automation, properties["automation"], {}),
            (Security, properties["security"], {}),
            (Rule, properties["rules"]["items"], {}),
            (DisabledGate, properties["gates"]["properties"]["disabled"]["items"], {}),
            (Unknown, properties["unknowns"]["items"], {}),
            (Input, properties["inputs"]["items"], {}),
        ]
        for cls, node, aliases in checks:
            for field in dataclasses.fields(cls):
                name = aliases.get(field.name, field.name)
                self.assertIn(name, node["properties"], f"{cls.__name__}.{field.name}")


if __name__ == "__main__":
    unittest.main()
