import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_kit import paths


class KitRootTests(unittest.TestCase):
    def test_missing_resources_message(self) -> None:
        with TemporaryDirectory() as temporary:
            fake = Path(temporary) / "ai_kit" / "paths.py"
            fake.parent.mkdir(parents=True)
            fake.write_text("", encoding="utf-8")
            with patch.object(paths, "__file__", str(fake)):
                with self.assertRaises(paths.KitRootError) as raised:
                    paths.kit_root()
            self.assertIn("pip install -e", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
