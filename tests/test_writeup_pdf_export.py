from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


pdf_builder = _module("build_writeup_pdf")


class WriteupPdfExportTests(unittest.TestCase):
    def test_pdf_is_reproducible_bounded_and_contains_required_structure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.pdf"
            second = Path(directory) / "second.pdf"
            first_stats = pdf_builder.build_pdf(ROOT / "docs/writeup/final-writeup.md", first)
            second_stats = pdf_builder.build_pdf(ROOT / "docs/writeup/final-writeup.md", second)

            self.assertEqual(first_stats["sha256"], second_stats["sha256"])
            self.assertLess(first_stats["bytes"], 10_000_000)
            self.assertGreaterEqual(first_stats["page_count"], 5)
            self.assertLessEqual(first_stats["page_count"], 80)
            self.assertGreater(first_stats["link_annotations"], 0)
            self.assertGreater(first_stats["embedded_images"], 0)
            self.assertEqual(first_stats["reportlab_version"], "4.4.10")
            self.assertEqual(first_stats["toolchain_versions"], {
                "reportlab": "4.4.10", "Pillow": "13.0.0", "charset-normalizer": "3.5.2"})

            inspector = shutil.which("pdftotext")
            if inspector:
                text = subprocess.run([inspector, "-layout", str(first), "-"],
                                      check=True, capture_output=True, text=True).stdout
                for required in ("Contents", "Finding 1", "Martin Holeček (Zindi: holi)", "V2_model_cgs",
                                 "hvLtPVmr", "B_from_A_cgs", "eAaWXWxM", "Page 1",
                                 f"Page {first_stats['page_count']}"):
                    self.assertIn(required, text)

            pdfinfo = shutil.which("pdfinfo")
            if pdfinfo:
                info = subprocess.run([pdfinfo, str(first)], check=True,
                                      capture_output=True, text=True).stdout
                self.assertIn(f"Pages:           {first_stats['page_count']}", info)

    def test_toolchain_lock_records_licenses_and_wheel_hashes(self) -> None:
        config = json.loads((ROOT / "config/writeup-submission-preregistered.json")
                            .read_text(encoding="utf-8"))
        lock = (ROOT / config["assembly"]["pdf_toolchain"]["requirements_file"])
        text = lock.read_text(encoding="utf-8")
        for package in config["assembly"]["pdf_toolchain"]["packages"]:
            self.assertIn(package["version"], text)
            self.assertIn(package["license"], text)
            self.assertIn(f"sha256:{package['wheel_sha256']}", text)

    def test_canonical_pdf_has_embedded_map_at_readable_source_resolution(self) -> None:
        pdf = ROOT / "docs/writeup/final-writeup.pdf"
        self.assertTrue(pdf.is_file())
        stats = pdf_builder.inspect_pdf(pdf)
        self.assertLess(stats["bytes"], 10_000_000)
        self.assertGreaterEqual(stats["page_count"], 5)
        self.assertGreater(stats["link_annotations"], 0)
        self.assertGreater(stats["embedded_images"], 0)
        image = ROOT / "reports/discovery-postfire-map.png"
        self.assertTrue(image.is_file())
        from PIL import Image
        with Image.open(image) as source:
            self.assertGreaterEqual(source.width, 1500)
            self.assertGreaterEqual(source.height, 700)


if __name__ == "__main__":
    unittest.main()
