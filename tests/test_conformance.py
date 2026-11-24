import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_kit.doctor import DoctorRequest, run
from ai_kit.init import Request, generate

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "conformance" / "cases"
EXAMPLE = ROOT / "profiles" / "example.toml"
PYTHON = sys.executable.replace("\\", "/")


def load_case(path: Path) -> dict:
    return json.loads((path / "case.json").read_text(encoding="utf-8"))


def copy_case(case_dir: Path, target: Path) -> None:
    for path in case_dir.rglob("*"):
        if not path.is_file() or path.name == "case.json":
            continue
        relative = path.relative_to(case_dir)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8").replace("@PYTHON@", PYTHON)
        destination.write_text(text, encoding="utf-8")


def apply_mutations(target: Path, mutations: list[dict]) -> None:
    for mutation in mutations:
        action = next(
            (name for name in ("delete", "append", "write", "replace") if name in mutation),
            None,
        )
        if action is None:
            raise AssertionError(f"неизвестная мутация: {mutation}")
        payload = mutation[action]
        relative = mutation.get("path") or payload["path"]
        path = target / relative
        if action == "delete":
            path.unlink()
        elif action == "append":
            path.write_text(path.read_text(encoding="utf-8") + payload["text"], encoding="utf-8")
        elif action == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload["text"], encoding="utf-8")
        else:
            text = path.read_text(encoding="utf-8")
            old = payload["old"]
            if old not in text:
                raise AssertionError(f"{path}: нет текста для замены: {old!r}")
            path.write_text(text.replace(old, payload["new"], 1), encoding="utf-8")


class ConformanceTests(unittest.TestCase):
    def test_corpus_is_not_empty(self) -> None:
        self.assertGreaterEqual(len(list(CASES.glob("*/case.json"))), 10)

    def test_every_case(self) -> None:
        for case_file in sorted(CASES.glob("*/case.json")):
            case_dir = case_file.parent
            with self.subTest(case=case_dir.name):
                self.check_case(case_dir)

    def check_case(self, case_dir: Path) -> None:
        case = load_case(case_dir)
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            copy_case(case_dir, target)
            adapter = target / case["adapter"] if case.get("adapter") else None
            profile = target / "profile.toml" if (target / "profile.toml").exists() else EXAMPLE
            generate(Request(target=target, kit_root=ROOT, profile=profile, adapter=adapter))
            apply_mutations(target, case.get("mutations", []))

            report = run(
                DoctorRequest(
                    root=target,
                    profile=target / "profile.toml",
                    adapter=adapter,
                    kit_root=ROOT,
                    gates=case.get("gates", False),
                )
            )
            self.assertEqual(report.exit_code, case["exit"], report.to_table())
            for expected in case.get("expect", []):
                self.assertTrue(
                    self.matches(expected, report),
                    f"не найдено {expected} в отчёте:\n{report.to_table()}",
                )

    def matches(self, expected: dict, report) -> bool:
        for item in report.diagnostics:
            if expected.get("code") and item.code != expected["code"]:
                continue
            if expected.get("severity") and item.severity != expected["severity"]:
                continue
            if expected.get("path") and expected["path"] not in item.path:
                continue
            if expected.get("message") and expected["message"] not in item.message:
                continue
            return True
        return False


if __name__ == "__main__":
    unittest.main()
