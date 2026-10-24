import csv
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_density_context_candidate import (  # noqa: E402
    CONFIG,
    aggregate_neighbors,
    density_support,
)


class DensityContextCandidateTests(unittest.TestCase):
    def test_fixed_density_normalization_is_bounded(self):
        self.assertEqual(density_support(0.0, 5000), 0.0)
        self.assertAlmostEqual(density_support(5000.0, 5000), 1.0)
        self.assertEqual(density_support(1e20, 5000), 1.0)
        self.assertIsNone(density_support(None, 5000))

    def test_neighborhood_aggregation_excludes_focal_tract(self):
        raw_neighbors = [0, 1, 2]
        original_values = [0.2, 0.4, 0.8]
        original = aggregate_neighbors(1, raw_neighbors, original_values)
        changed_values = [0.2, 1000000.0, 0.8]
        changed = aggregate_neighbors(1, raw_neighbors, changed_values)
        self.assertAlmostEqual(original, 0.5)
        self.assertEqual(original, changed)
        self.assertIsNone(aggregate_neighbors(1, [1], changed_values))

    def test_generated_template_candidate_hash_bounds_and_invariance(self):
        report_path = REPO / "reports/density-context-candidate.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        candidate_path = REPO / report["candidate_csv"]
        manifest_path = REPO / report["candidate_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(report["candidate_id"], CONFIG["candidate_id"])
        self.assertEqual(report["candidate_rows"], 9794)
        self.assertEqual(manifest["candidate_id"], CONFIG["candidate_id"])
        self.assertEqual(manifest["prior_candidate_sha256"], CONFIG["prior_candidate"]["sha256"])
        self.assertEqual(hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
                         report["candidate_csv_sha256"])
        self.assertEqual(hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                         report["candidate_manifest_sha256"])
        self.assertTrue(report["measure_after"]["leave_one_tract_out_invariance_all_regions"])
        self.assertTrue(report["measure_after"]["exact_candidate_ids_and_bounds"][
            "exact_template_order_once"])
        self.assertTrue(report["measure_after"]["exact_candidate_ids_and_bounds"][
            "maximum_adjustment_at_most_0_05"])
        self.assertFalse(report["restrictions"]["sample_score_placeholders_read_or_used"])
        self.assertFalse(report["restrictions"]["removed_reference_layers_or_equivalents_read"])
        self.assertFalse(report["restrictions"]["upload_or_submission_made"])

        with candidate_path.open("r", newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        with (REPO / "data/reproduction/template-geoid-region.csv").open(
                "r", newline="", encoding="utf-8") as stream:
            template_ids = [row["GEOID"] for row in csv.DictReader(stream)]
        self.assertEqual([row["GEOID"] for row in rows], template_ids)
        self.assertEqual(len(set(row["GEOID"] for row in rows)), 9794)
        for row in rows:
            for column in ("prior_coverage_gap_score", "density_context_support",
                           "population_support_5km", "building_support_5km",
                           "population_support_20km", "building_support_20km",
                           "density_context_coverage_gap_score"):
                value = float(row[column])
                self.assertTrue(math.isfinite(value))
                self.assertTrue(0 <= value <= 1)
            for column in ("proposed_density_adjustment", "candidate_delta"):
                value = float(row[column])
                self.assertTrue(math.isfinite(value))
                self.assertTrue(0 <= value <= 0.05 + 1e-12)
        wa_rows = [row for row in rows if row["region"] == "eastern-wa"]
        self.assertEqual(len(wa_rows), 415)
        self.assertTrue(all(float(row["prior_coverage_gap_score"]) == 0.0 and
                            float(row["density_context_coverage_gap_score"]) == 0.0 and
                            float(row["candidate_delta"]) == 0.0 for row in wa_rows))


if __name__ == "__main__":
    unittest.main()
