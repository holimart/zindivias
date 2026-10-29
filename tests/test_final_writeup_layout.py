import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = ROOT / "docs/writeup/final-writeup.md"
PDF = ROOT / "docs/writeup/final-writeup.pdf"


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w’'—]+\b", text))


class FinalWriteupLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = MARKDOWN.read_text(encoding="utf-8")
        cls.discovery_start = cls.text.index("## Best Bias Discovery entry")
        cls.method_start = cls.text.index("## Methodology (Best Documentation entry)")
        cls.appendix_start = cls.text.index("## Appendix A: Detailed methodology")
        cls.discovery = cls.text[cls.discovery_start:cls.method_start]
        cls.methodology = cls.text[cls.method_start:cls.appendix_start]

    def test_core_word_caps(self) -> None:
        self.assertLessEqual(word_count(self.discovery), 1500)
        self.assertLessEqual(word_count(self.methodology), 3000)
        self.assertLessEqual(word_count(self.text[self.discovery_start:self.appendix_start]), 1600)

    def test_discovery_has_two_tables_and_one_map(self) -> None:
        self.assertEqual(sum(line.startswith("|---") for line in self.discovery.splitlines()), 2)
        self.assertEqual(sum(line.startswith("![") for line in self.discovery.splitlines()), 1)
        self.assertIn("reports/discovery-postfire-map.png", self.discovery)

    def test_lead_order_and_appendices_are_linked(self) -> None:
        self.assertLess(self.text.index("## Submission context"), self.discovery_start)
        self.assertLess(self.text.index("## Contents"), self.discovery_start)
        self.assertLess(self.discovery_start, self.method_start)
        self.assertLessEqual(word_count(self.text[:self.appendix_start]), 6000)
        for phrase in ("PENDING_OPERATOR_CONFIRMATION", "NOT SUBMITTED",
                       "staged draft", "not submission-ready", "no UI selection was made"):
            self.assertNotIn(phrase.lower(), self.text.lower())
        self.assertIn("appendix-diagnostics.md", self.text)
        self.assertIn("methodology.md", self.text)
        self.assertIn("edge-cases.md", self.text)

    @unittest.skipUnless(shutil.which("pdfimages"), "pdfimages is required to inspect embedded figures")
    def test_map_is_embedded_in_pdf(self) -> None:
        result = subprocess.run(["pdfimages", "-list", str(PDF)], check=True,
                                capture_output=True, text=True)
        self.assertIn("image", result.stdout)
        self.assertIn("2250", result.stdout)


if __name__ == "__main__":
    unittest.main()
