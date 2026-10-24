import csv
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_cross_theme_support_candidate import (  # noqa: E402
    CONFIG,
    SUPPORT_NAMES,
    adjusted_score,
    correlation,
    cross_theme_support,
    support_features,
)


class CrossThemeSupportTests(unittest.TestCase):
    def test_support_features_are_bounded_and_missingness_explicit(self):
        row = {
            "overture_buildings": 100,
            "overture_building_microsoft_source_share": 0.6,
            "overture_named_road_km_5070": 10.0,
            "overture_route_km": 5.0,
            "overture_poi_all": 20,
        }
        confidence = {"confidence_valid_count": 20, "confidence_below_0_5_share": 0.25}
        features, missing, raw = support_features(row, confidence)
        self.assertEqual(features["building_source_support"], 0.6)
        self.assertAlmostEqual(features["road_route_support"], 1 / 3)
        self.assertEqual(features["place_confidence_support"], 0.75)
        self.assertEqual(raw["place_confidence_support"], 0.75)
        self.assertEqual(set(features), set(SUPPORT_NAMES))
        self.assertEqual(sum(missing.values()), 0)

        row.update({"overture_buildings": 0,
                    "overture_building_microsoft_source_share": None,
                    "overture_named_road_km_5070": 0.0,
                    "overture_route_km": 0.0,
                    "overture_poi_all": 0})
        confidence = {"confidence_valid_count": 0, "confidence_below_0_5_share": None}
        features, missing, raw = support_features(row, confidence)
        self.assertEqual(set(features.values()), {0.5})
        self.assertEqual(set(missing.values()), {1})

    def test_consistency_support_and_positive_adjustment_are_fixed(self):
        values = dict(zip(SUPPORT_NAMES, (0.8, 0.6, 0.4)))
        mean_support, weakest, cross = cross_theme_support(values)
        self.assertAlmostEqual(mean_support, 0.6)
        self.assertAlmostEqual(weakest, 0.4)
        self.assertAlmostEqual(cross, 0.5)
        adjustment, score = adjusted_score(0.2, cross)
        self.assertAlmostEqual(adjustment, 0.025)
        self.assertAlmostEqual(score, 0.225)
        adjustment, score = adjusted_score(0.99, 0.0)
        self.assertAlmostEqual(adjustment, 0.05)
        self.assertEqual(score, 1.0)
        self.assertEqual(adjusted_score(0.0, 1.0), (0.0, 0.0))

    def test_pairwise_correlations_use_complete_pairs_and_tie_ranks(self):
        result = correlation([0, 1, 1, 3], [1, 2, 2, 4])
        self.assertEqual(result["n"], 4)
        self.assertAlmostEqual(result["pearson"], 1.0)
        self.assertAlmostEqual(result["spearman"], 1.0)
        constant = correlation([1, 1], [2, 3])
        self.assertIsNone(constant["pearson"])
        self.assertEqual(constant["reason"], "constant input")

    def test_generated_artifacts_hash_ids_bounds_and_gate(self):
        report = json.loads((REPO / "reports/cross-theme-support.json").read_text(encoding="utf-8"))
        manifest_path = REPO / report["candidate_manifest"]
        candidate_path = REPO / report["candidate_csv"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(report["candidate_id"], CONFIG["candidate_id"])
        self.assertEqual(report["candidate_rows"], 9379)
        self.assertEqual(hashlib.sha256(candidate_path.read_bytes()).hexdigest(), report["candidate_csv_sha256"])
        self.assertEqual(hashlib.sha256(manifest_path.read_bytes()).hexdigest(), report["candidate_manifest_sha256"])
        self.assertEqual(manifest["prior_candidate_sha256"], CONFIG["prior_candidate"]["sha256"])
        self.assertEqual(manifest["candidate_id"], CONFIG["candidate_id"])
        self.assertTrue(report["global"]["all_support_and_score_rows_finite_bounded_0_1"])
        self.assertTrue(report["global"]["max_absolute_adjustment_within_preregistered_0_05"])
        self.assertLessEqual(report["global"]["max_absolute_adjustment"], 0.05)
        self.assertFalse(report["restrictions"]["official_sample_score_placeholders_read_or_used"])
        self.assertFalse(report["restrictions"]["removed_reference_layers_or_equivalents_read"])
        self.assertFalse(report["restrictions"]["upload_or_submission_made"])
        self.assertFalse(report["restrictions"]["accuracy_or_target_improvement_claim"])
        with candidate_path.open("r", newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 9379)
        self.assertEqual(len({row["GEOID"] for row in rows}), 9379)
        for row in rows:
            for name in ("building_source_support", "road_route_support", "place_confidence_support",
                         "mean_theme_support", "weakest_theme_support", "cross_theme_support",
                         "cross_theme_adjustment", "prior_coverage_gap_score", "adjusted_coverage_gap_score"):
                value = float(row[name])
                self.assertTrue(math.isfinite(value))
                if name != "cross_theme_adjustment":
                    self.assertTrue(0 <= value <= 1)
                else:
                    self.assertTrue(0 <= value <= 0.05)


if __name__ == "__main__":
    unittest.main()
