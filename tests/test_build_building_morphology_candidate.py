import csv
import hashlib
import json
import math
import unittest
from pathlib import Path
import sys

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_building_morphology_candidate import (
    CONFIG,
    morphology_gap,
    morphology_support,
    missingness_summary,
    subtype_present,
)
import pyarrow as pa


class BuildingMorphologyCandidateTests(unittest.TestCase):
    def test_fixed_coefficients_and_complete_support(self):
        self.assertAlmostEqual(sum(CONFIG["morphology_support"]["weights"].values()), 1.0)
        score, terms = morphology_support(
            building_count=1000,
            land_area_m2=1_000_000,
            median_area_m2=500,
            verticality=1,
            subtype_share=1,
            has_parts_share=1,
            population=10_000,
        )
        self.assertTrue(all(value == 1 for value in terms.values()))
        self.assertAlmostEqual(score, 1.0)
        self.assertAlmostEqual(morphology_gap(score, 0.05, True), 0.01)

    def test_low_support_sensitivity_is_bounded_and_undefined_is_explicit(self):
        self.assertAlmostEqual(morphology_gap(0.0, 0.05, True), 0.06)
        self.assertEqual(morphology_gap(0.0, 0.05, False), 0.0)
        for beta in CONFIG["component_rule"]["sensitivity_betas"]:
            values = [morphology_gap(support, beta, True) for support in np.linspace(0, 1, 101)]
            self.assertTrue(all(math.isfinite(value) and 0 <= value <= 0.1 for value in values))

    def test_subtype_missingness_uses_nonempty_trimmed_values(self):
        result = subtype_present(pa.array(["residential", "", None, "  office  "]))
        self.assertEqual(result.tolist(), [True, False, False, True])

    def test_attribute_missingness_is_building_weighted_and_bounded(self):
        result = missingness_summary(np.array([3, 2]), np.array([1, 2]))
        self.assertEqual(result["missing_rows"], 2)
        self.assertAlmostEqual(result["missing_share"], 0.4)
        self.assertEqual(missingness_summary(np.array([0]), np.array([0]))["missing_share"], 0.0)

    def test_invalid_numeric_context_is_rejected(self):
        with self.assertRaises(ValueError):
            morphology_support(
                building_count=1, land_area_m2=1, median_area_m2=1,
                verticality=float("nan"), subtype_share=1,
                has_parts_share=0, population=1,
            )

    def test_generated_candidate_is_exact_scope_finite_and_analysis_only(self):
        report = json.loads((REPO / "reports/building-morphology-candidate.json").read_text(encoding="utf-8"))
        candidate = REPO / report["candidate_csv"]
        self.assertEqual(report["candidate_rows"], 9379)
        self.assertEqual(hashlib.sha256(candidate.read_bytes()).hexdigest(), report["candidate_csv_sha256"])
        self.assertFalse(report["restrictions"]["score_placeholders_or_target_labels_read"])
        self.assertFalse(report["restrictions"]["upload_or_submission_made"])
        self.assertEqual(report["global_sensitivity_by_beta"]["0.02"]["changed_vs_constant_baseline_tracts"], 9377)
        self.assertTrue(report["global_sensitivity_by_beta"]["0.02"]["bounded_to_preregistered_range"])
        with candidate.open("r", newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 9379)
        for row in rows:
            for field in ("building_gap_constant_0_01", "building_gap_morph_beta_0.01",
                          "building_gap_morph_beta_0.02", "building_gap_morph_beta_0.05"):
                value = float(row[field])
                self.assertTrue(math.isfinite(value) and 0 <= value <= 0.1)


if __name__ == "__main__":
    unittest.main()
