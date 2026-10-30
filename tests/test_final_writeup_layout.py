import importlib.util
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "docs/writeup/final-writeup.pdf"


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w’'—]+\b", text))


class FinalWriteupLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Assemble current sources in memory; do not rebuild the staged packet while review edits remain open.
        name = "_final_writeup_layout_builder"
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/build_final_writeup.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load final writeup builder")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        cls.text, _ = module.assemble(module.CONFIG)
        cls.discovery_start = cls.text.index("## Best Bias Discovery entry")
        cls.related_start = cls.text.index("## Related entries on the challenge board")
        cls.method_start = cls.text.index("## Methodology (Best Documentation entry)")
        cls.appendix_start = cls.text.index("## Appendix A: Detailed methodology")
        cls.discovery = cls.text[cls.discovery_start:cls.related_start]
        cls.related = cls.text[cls.related_start:cls.method_start]
        cls.methodology = cls.text[cls.method_start:cls.appendix_start]

    def test_core_word_caps(self) -> None:
        self.assertLessEqual(word_count(self.discovery), 1750)
        self.assertLessEqual(word_count(self.related), 500)
        self.assertLessEqual(word_count(self.methodology), 3000)
        self.assertLessEqual(word_count(self.text[self.discovery_start:self.appendix_start]), 2650)

    def test_author_and_repository_notice_render_in_staged_writeup(self) -> None:
        markdown_path = ROOT / "docs/writeup/final-writeup.md"
        markdown = markdown_path.read_text(encoding="utf-8")
        lines = markdown.splitlines()
        self.assertEqual(lines[0], "# Bias Bounty Mapping Equity Challenge — discovery and methodology")
        self.assertEqual(lines[1], "Martin Holeček (Zindi: holi) — 2026-10-30")
        self.assertIn("submission repository: https://github.com/holimart/zindivias (CC-BY-SA 4.0)", markdown)
        pdftotext = shutil.which("pdftotext")
        if pdftotext:
            extracted = subprocess.run([pdftotext, "-layout", str(PDF), "-"], check=True,
                                       capture_output=True, text=True).stdout
            self.assertIn("Martin Holeček (Zindi: holi) — 2026-10-30", extracted)
            self.assertGreaterEqual(extracted.count("Martin Holeček (Zindi: holi)"), 2)
            self.assertIn("reports/*.json", extracted)
            pdfinfo = shutil.which("pdfinfo")
            if pdfinfo:
                info = subprocess.run([pdfinfo, str(PDF)], check=True,
                                      capture_output=True, text=True).stdout
                self.assertRegex(info, r"(?m)^Author:\s+Martin Holeček \(Zindi: holi\)$")

    def test_discovery_has_two_tables_and_one_map(self) -> None:
        self.assertEqual(sum(line.startswith("|---") for line in self.discovery.splitlines()), 2)
        self.assertIn("Does it transfer?", self.discovery)
        self.assertIn("Google-only", self.discovery)
        self.assertEqual(sum(line.startswith("![") for line in self.discovery.splitlines()), 1)
        self.assertIn("reports/discovery-postfire-map.png", self.discovery)

    def test_lead_order_and_appendices_are_linked(self) -> None:
        summary_start = self.text.index("## Executive summary")
        submission_start = self.text.index("## Submission context")
        self.assertLess(self.text.index("# Bias Bounty Mapping Equity Challenge"), summary_start)
        self.assertLess(summary_start, submission_start)
        summary = self.text[summary_start:submission_start]
        self.assertEqual(sum(line.startswith("- **") for line in summary.splitlines()), 7)
        for marker in ("16.8–38.7 pp", "0.092 vs 0.615", "0.620–0.747",
                       "805 of 858", "0.330 in El Paso", "CDC PLACES 2025",
                       "V2_model_cgs", "provided features only",
                       "reports/source-provenance.json",
                       "reports/discovery-border-metros.json#/results",
                       "reports/discovery-postfire.json#/focal_butte_2018_burn_geoids",
                       "reports/discovery-dins-mechanism.json#/decision",
                       "reports/discovery-strata-confound.json#/stratum_composition",
                       "reports/discovery-border-transfer.json#/results",
                       "external-discovery-2026-10-29-fable/cdc-places-2025-tx/MANIFEST.json",
                       "reports/final-two.json#/final_candidates"):
            self.assertIn(marker, summary)
        contents = self.text[self.text.index("## Contents"):self.discovery_start]
        self.assertIn("[Related entries on the challenge board](#related-entries-on-the-challenge-board)",
                      contents)
        self.assertLess(submission_start, self.text.index("## Contents"))
        self.assertLess(self.text.index("## Contents"), self.discovery_start)
        self.assertLess(self.discovery_start, self.related_start)
        self.assertLess(self.text.index("### Finding 4"), self.related_start)
        self.assertLess(self.related_start, self.method_start)
        self.assertIn("### Finding 1 — Rural tracts have lower OSM-attributed shares", self.discovery)
        self.assertIn("### Finding 2 — Rio Grande border metros", self.discovery)
        self.assertLessEqual(word_count(self.text[:self.appendix_start]), 6000)
        for phrase in ("PENDING_OPERATOR_CONFIRMATION", "NOT SUBMITTED",
                       "staged draft", "not submission-ready", "no UI selection was made"):
            self.assertNotIn(phrase.lower(), self.text.lower())
        self.assertIn("appendix-diagnostics.md", self.text)
        self.assertIn("methodology.md", self.text)
        self.assertIn("edge-cases.md", self.text)

    def test_rural_finding_numbers_and_named_tracts_trace_to_report(self) -> None:
        report = json.loads((ROOT / "reports/source-provenance.json").read_text(encoding="utf-8"))
        rural = self.discovery.split("### Finding 1", 1)[1].split("### Finding 2", 1)[0]
        appendix = self.text.split("## Appendix I: Named high-SVI tracts with lowest OSM attribution", 1)[1].split(
            "## Appendix J: Supporting discovery tables", 1)[0]
        self.assertIn("e80171d07d555e8e77e533b35b8ae4ca71aee481e64f618d8a6dc63df0512565", rural)
        named_rows = re.findall(
            r"^\| ([^|]+) \| `(\d{11})` \| ([^|]+) \| ([^|]+) \| ([0-9.]+)% \|$",
            appendix, re.MULTILINE)
        named_shares = {geoid: share for _, geoid, _, _, share in named_rows}
        expected_shares = {}
        for region, content in report["regions"].items():
            contrast = content["stratum_contrasts"]["rural_minus_urban"]["building_osm_share"]
            self.assertIn(f"{100 * contrast['group1_mean']:.2f}%", rural)
            self.assertIn(f"{100 * contrast['group0_mean']:.2f}%", rural)
            self.assertIn(f"{100 * contrast['difference']:.2f}".replace("-", "−"), rural)
            for bound in contrast["ci_95"]:
                self.assertIn(f"{100 * bound:.2f}".replace("-", "−"), rural)
            nr = contrast["group1_tracts_with_source_denominator"]
            nu = contrast["group0_tracts_with_source_denominator"]
            self.assertIn(f"{nr + nu:,} ({nr:,}, {nu:,})", rural)
            expected_shares.update({item["GEOID"]: f"{100 * item['building_osm_source_share']:.3f}"
                                    for item in content["named_lowest_osm_high_svi"]})
        self.assertEqual(named_shares, expected_shares)
        self.assertIn("Mappers should treat the named tracts as partner-led review priorities", rural)
        self.assertIn("dispatch/evacuation outcomes were not measured", rural.lower())

    @unittest.skipUnless(shutil.which("pdfimages"), "pdfimages is required to inspect embedded figures")
    def test_map_is_embedded_in_pdf(self) -> None:
        result = subprocess.run(["pdfimages", "-list", str(PDF)], check=True,
                                capture_output=True, text=True)
        self.assertIn("image", result.stdout)
        self.assertIn("2250", result.stdout)


if __name__ == "__main__":
    unittest.main()
