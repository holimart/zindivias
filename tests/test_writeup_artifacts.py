import json
import tempfile
import unittest
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from audit_writeup_edge_cases import water_proxy_share
from render_probe_log import EVENTS_PATH, render, score_display


class WriteupArtifactTests(unittest.TestCase):
    def test_sample_id_reader_does_not_parse_placeholders(self):
        # Import locally so the fixture can prove nonnumeric placeholders are not consumed.
        from audit_writeup_edge_cases import csv_ids

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.csv"
            path.write_text(
                "GEOID,transport_gap,building_gap,poi_gap,coverage_gap_score\n"
                "00001000100,not-a-score,not-a-score,not-a-score,not-a-score\n",
                encoding="utf-8",
            )
            self.assertEqual(csv_ids(path), ["00001000100"])

    def test_generated_edge_report_records_named_scope_edges(self):
        report = json.loads((REPO / "reports/writeup-edge-cases.json").read_text(encoding="utf-8"))
        self.assertTrue(report["regions"]["maricopa-az"]["new_mexico_geoid_35023970000_present"])
        self.assertEqual(report["regions"]["south-central-tx"]["strata_extra_rows_excluded"], 7)
        self.assertEqual(report["regions"]["south-central-tx"]["zero_population"], 14)

    def test_water_proxy_bounds_and_zero_area(self):
        self.assertEqual(water_proxy_share(50, 100), 0.5)
        self.assertEqual(water_proxy_share(150, 100), 0.0)
        self.assertEqual(water_proxy_share(0, 100), 1.0)
        self.assertIsNone(water_proxy_share(0, 0))

    def test_only_successful_ledger_rows_show_scores(self):
        self.assertEqual(score_display({"status": "initial", "our_score": 0.0}),
                         "not a score (initial platform placeholder; see correction)")
        self.assertEqual(score_display({"status": "successful", "our_score": 0.25}), "0.250000000")
        self.assertEqual(score_display({"status": "upload_failed_http_400", "our_score": None}), "—")

    def test_probe_log_covers_all_ledger_events_and_moments(self):
        events = [json.loads(line) for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines()]
        rendered = render()
        self.assertIn(f"**Ledger events:** {len(events)}.", rendered)
        self.assertIn("ggVY44Ui", rendered)
        self.assertIn("uRRw9KpB", rendered)
        self.assertIn("E[p²]=0.029048967; inferred E[p y]=0.009551697", rendered)
        self.assertIn("P5's upload was rejected", rendered)
        self.assertIn("No observed subset moment", rendered)
        self.assertNotIn("0.000000000 |", rendered)


if __name__ == "__main__":
    unittest.main()
