import re
import unittest
from pathlib import Path


class WebFontStyleTests(unittest.TestCase):
    def test_global_font_is_sans_serif_and_controls_inherit(self) -> None:
        css = Path("web/src/styles/main.css").read_text(encoding="utf-8")
        self.assertIn('"Noto Sans SC", sans-serif', css)
        self.assertIn("button, input, textarea, select { font: inherit; }", css)
        self.assertIsNone(re.search(r"font-family\s*:\s*serif\b", css))


if __name__ == "__main__":
    unittest.main()
