import unittest

from ai_kit import inputs


def entry(**overrides):
    data = {"id": "health", "kind": "http", "locator": "GET /health"}
    data.update(overrides)
    return data


class InputTests(unittest.TestCase):
    def test_valid_entry(self) -> None:
        parsed = inputs.parse([entry(feature="health.md")])
        self.assertEqual(parsed[0].feature, "features/health.md")

    def test_canonical_feature(self) -> None:
        self.assertEqual(inputs.canonical_feature("features/health.md"), "features/health.md")
        self.assertIsNone(inputs.canonical_feature("features/admin/health.md"))
        self.assertIsNone(inputs.canonical_feature("../health.md"))
        self.assertIsNone(inputs.canonical_feature("health\\health.md"))
        self.assertIsNone(inputs.canonical_feature("health.txt"))

    def test_duplicate_id_rejected(self) -> None:
        with self.assertRaises(inputs.InputError) as raised:
            inputs.parse([entry(), entry()])
        self.assertIn("inputs[1].id", [path for path, _ in raised.exception.errors])

    def test_bad_id_rejected(self) -> None:
        with self.assertRaises(inputs.InputError) as raised:
            inputs.parse([entry(id="Health")])
        self.assertIn("inputs[0].id", [path for path, _ in raised.exception.errors])

    def test_empty_kind_rejected(self) -> None:
        with self.assertRaises(inputs.InputError) as raised:
            inputs.parse([entry(kind=" ")])
        self.assertIn("inputs[0].kind", [path for path, _ in raised.exception.errors])

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(inputs.InputError) as raised:
            inputs.parse([entry(route="GET /health")])
        self.assertIn("inputs[0].route", [path for path, _ in raised.exception.errors])

    def test_apply_seeds_empty_profile(self) -> None:
        values = inputs.apply({}, {"inputs": [entry()]})
        self.assertEqual(values["inputs"], [entry()])

    def test_apply_without_adapter_is_noop(self) -> None:
        values = {"inputs": [entry()]}
        self.assertEqual(inputs.apply(values, {}), values)

    def test_apply_same_inputs_keeps_profile(self) -> None:
        values = {"inputs": [entry()]}
        self.assertEqual(inputs.apply(values, {"inputs": [entry()]}), values)

    def test_apply_conflict_is_error(self) -> None:
        values = {"inputs": [entry()]}
        with self.assertRaises(inputs.InputError):
            inputs.apply(values, {"inputs": [entry(id="other")]})

    def test_empty_profile_inputs_is_authoritative(self) -> None:
        with self.assertRaises(inputs.InputError):
            inputs.apply({"inputs": []}, {"inputs": [entry()]})

    def test_apply_does_not_mutate_source(self) -> None:
        values: dict = {}
        inputs.apply(values, {"inputs": [entry()]})
        self.assertEqual(values, {})


if __name__ == "__main__":
    unittest.main()
