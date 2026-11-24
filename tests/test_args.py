import unittest

from ai_kit.args import UsageError, ensure_known, normalize


class ArgsTests(unittest.TestCase):
    def test_value_starting_with_dash_is_allowed(self) -> None:
        ensure_known(["--profile", "-file.toml"], {"--profile"}, {"--profile"})

    def test_unknown_flag_still_rejected(self) -> None:
        with self.assertRaises(UsageError):
            ensure_known(["--profile", "--typo"], {"--profile"}, {"--profile"})
        with self.assertRaises(UsageError):
            ensure_known(["--typo"], {"--profile"}, {"--profile"})

    def test_known_flag_cannot_be_value(self) -> None:
        with self.assertRaises(UsageError):
            ensure_known(
                ["--write-profile", "--dry-run"],
                {"--write-profile", "--dry-run"},
                {"--write-profile"},
            )

    def test_equals_is_normalized(self) -> None:
        self.assertEqual(normalize(["--profile=-file.toml"]), ["--profile", "-file.toml"])


if __name__ == "__main__":
    unittest.main()
