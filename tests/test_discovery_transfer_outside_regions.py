import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts/discovery"))
from transfer_outside_regions import is_google_only, summarize_tracts


class TransferOutsideRegionsTests(unittest.TestCase):
    def test_google_only_is_a_single_source_class(self):
        self.assertTrue(is_google_only({"google open buildings"}))
        self.assertFalse(is_google_only({"google open buildings", "microsoft ml buildings"}))
        self.assertFalse(is_google_only({"openstreetmap"}))
        self.assertFalse(is_google_only(set()))

    def test_tract_summary_uses_footprint_shares_and_strict_five_percent_cut(self):
        result = summarize_tracts({
            "tract-a": {"footprints": 100, "osm": 4, "microsoft_only": 40,
                         "google_only": 30, "esri": 10, "other": 30},
            "tract-b": {"footprints": 10, "osm": 1, "microsoft_only": 5,
                         "google_only": 2, "esri": 1, "other": 2},
        })
        self.assertEqual(result["footprints_in_county"], 110)
        self.assertEqual(result["tracts_with_footprints"], 2)
        self.assertAlmostEqual(result["osm_touched_share"], 5 / 110)
        self.assertAlmostEqual(result["tract_median_osm_share"], 0.07)
        self.assertEqual(result["tracts_osm_below_5pct"], 1)

    def test_rerun_report_meets_transfer_decision_rule(self):
        report = json.loads((REPO / "reports/discovery-border-transfer.json").read_text())
        self.assertEqual(report["source"]["release"], "2026-09-23.1")
        self.assertEqual(report["analysis_script"]["path"], "scripts/discovery/transfer_outside_regions.py")
        self.assertFalse(report["data_and_restrictions"]["candidate_artifacts_or_predictions_read"])
        context = report["other_source_context"]
        self.assertEqual(context["near_equal_count_differences"]["border_other_minus_google"], -526)
        self.assertEqual(context["near_equal_count_differences"]["nonborder_other_minus_usgs"], 526)
        self.assertAlmostEqual(context["bexar_cache"]["footprint_weighted_other_source_share"], .336, delta=.0006)
        rows = {row["county_fips"]: row for row in report["results"]}
        expected = {
            "48141": (392897, .093, .475, .330, .000, 101, 188),
            "06025": (80116, .200, .437, .292, .000, 17, 40),
            "35013": (118361, .179, .419, .108, .261, 29, 61),
            "48135": (78050, .067, .456, .000, .477, 18, 33),
            "35001": (282316, .809, .191, .000, .000, 2, 176),
        }
        for fips, values in expected.items():
            row = rows[fips]
            n, osm, ms, google, esri, low, total = values
            self.assertEqual(row["footprints_in_county"], n)
            for key, value in (("osm_touched_share", osm), ("microsoft_only_share", ms),
                               ("google_only_share", google), ("esri_touched_share", esri)):
                self.assertAlmostEqual(row[key], value, delta=.0006, msg=f"{fips} {key}")
            self.assertEqual(row["tracts_osm_below_5pct"], low)
            self.assertEqual(row["tracts_osm_below_5pct_denominator"], total)
            self.assertEqual(len(row["bbox_file_sha256"]), 64)
        for fips in ("48141", "06025", "35013"):
            self.assertGreater(rows[fips]["google_only_share"], 0.10)
        for fips in ("48135", "35001"):
            self.assertEqual(rows[fips]["google_only_share"], 0.0)
        self.assertLess(rows["48135"]["osm_touched_share"], rows["48141"]["osm_touched_share"])
        self.assertGreater(rows["48135"]["esri_touched_share"], .45)

        writeup = (REPO / "docs/writeup/bias-discovery.md").read_text()
        transfer_section = writeup.split("### Does it transfer?", 1)[1].split(
            "#35031/#35474", 1
        )[0]
        self.assertIn("| County | OSM | Google-only | Esri |", transfer_section)
        self.assertIn("Google-only is 10.8–33.0%", transfer_section)
        appendix = (REPO / "docs/writeup/appendices/bias-discovery-supporting-tables.md").read_text()
        detailed_table = appendix.split("### Out-of-region border transfer", 1)[1]
        rendered = {
            "48141": "| **El Paso TX** | Border, unscored",
            "06025": "| **Imperial CA** | Border, unscored",
            "35013": "| **Doña Ana NM** | Border, unscored",
            "48135": "| Ector TX (Odessa) | Non-border, Hispanic-majority control",
            "35001": "| Bernalillo NM (Albuquerque) | Non-border control",
        }
        for fips, prefix in rendered.items():
            row = rows[fips]
            table_line = next(line for line in detailed_table.splitlines()
                              if line.startswith(prefix))
            expected = (f"{row['footprints_in_county']:,} | {row['osm_touched_share']:.3f} | "
                        f"{row['microsoft_only_share']:.3f} | **{row['google_only_share']:.3f}** | "
                        f"{row['esri_touched_share']:.3f} | {row['tract_median_osm_share']:.3f} | "
                        f"{row['tracts_osm_below_5pct']} / {row['tracts_osm_below_5pct_denominator']} |")
            self.assertIn(expected, table_line)


if __name__ == "__main__":
    unittest.main()
