import json
import unittest

from ai_kit.diagnostics import Diagnostic, Report


class ReportTests(unittest.TestCase):
    def test_empty_report_is_ok(self) -> None:
        report = Report(())
        self.assertTrue(report.ok)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.to_table(), "Нарушений нет.")

    def test_error_makes_report_fail(self) -> None:
        report = Report((Diagnostic("link", "error", "a.md", "нет цели"),))
        self.assertFalse(report.ok)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("ошибка", report.to_table())

    def test_gap_does_not_fail(self) -> None:
        report = Report((Diagnostic("gate", "gap", "layers", "не реализован"),))
        self.assertTrue(report.ok)
        self.assertEqual(report.gaps, 1)

    def test_sorting_puts_errors_first(self) -> None:
        report = Report(
            (
                Diagnostic("gate", "gap", "g", "пробел"),
                Diagnostic("section", "warning", "w", "пусто"),
                Diagnostic("link", "error", "e", "битая"),
            )
        )
        self.assertEqual([item.severity for item in report.sorted()], ["error", "warning", "gap"])

    def test_json_is_parsed_back(self) -> None:
        report = Report((Diagnostic("link", "error", "a.md", "нет цели"),))
        data = json.loads(report.to_json())
        self.assertEqual(data["summary"]["errors"], 1)
        self.assertEqual(data["diagnostics"][0]["code"], "link")

    def test_unknown_severity_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Diagnostic("x", "info", "a", "b")


if __name__ == "__main__":
    unittest.main()
