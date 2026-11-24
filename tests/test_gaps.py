import unittest

from ai_kit import gaps


def entry(**overrides):
    data = {
        "id": "layers",
        "reason": "нет проверки",
        "owner": "владелец",
        "trigger": "второй разработчик",
    }
    data.update(overrides)
    return data


class GapTests(unittest.TestCase):
    def test_valid_gap(self) -> None:
        parsed = gaps.load([entry()])
        self.assertEqual(parsed[0].id, "layers")

    def test_dash_trigger_rejected(self) -> None:
        with self.assertRaises(gaps.GapError) as raised:
            gaps.load([entry(trigger="—")])
        self.assertIn("gaps[0].trigger", [path for path, _ in raised.exception.errors])

    def test_duplicate_id_rejected(self) -> None:
        with self.assertRaises(gaps.GapError) as raised:
            gaps.load([entry(), entry()])
        self.assertIn("gaps[1].id", [path for path, _ in raised.exception.errors])

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(gaps.GapError) as raised:
            gaps.load([entry(note="x")])
        self.assertIn("gaps[0].note", [path for path, _ in raised.exception.errors])

    def test_empty_field_rejected(self) -> None:
        with self.assertRaises(gaps.GapError) as raised:
            gaps.load([entry(owner=" ")])
        self.assertIn("gaps[0].owner", [path for path, _ in raised.exception.errors])

    def test_not_a_list_rejected(self) -> None:
        with self.assertRaises(gaps.GapError):
            gaps.load("x")


if __name__ == "__main__":
    unittest.main()
