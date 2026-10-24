import csv
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_building_source_prior import (  # noqa: E402
    BETAS,
    CONFIG,
    PROFILES,
    REGIONS,
    component_gap,
    population_context,
    source_support,
    total_support,
)


class BuildingSourcePriorTests(unittest.TestCase):
    def test_preregistered_source_profiles_are_bounded_and_complete(self):
        classes = set(CONFIG["source_class_order"])
        self.assertEqual(set(PROFILES), {"conservative", "primary", "microsoft_heavy"})
        for profile in PROFILES.values():
            self.assertEqual(set(profile), classes)
            self.assertTrue(all(0 <= value <= 1 for value in profile.values()))
        self.assertEqual(BETAS, (0.01, 0.02, 0.05))
        self.assertEqual(CONFIG["component_rule"]["primary_profile"], "primary")

    def test_source_mix_is_a_partition_and_support_obeys_frozen_weights(self):
        shares = {
            "microsoft_only": 0.4,
            "microsoft_osm": 0.2,
            "osm_only": 0.1,
            "esri_only": 0.1,
            "other_or_mixed": 0.1,
            "no_dataset": 0.1,
        }
        profile = PROFILES["primary"]
        expected = sum(shares[name] * profile[name] for name in shares)
        self.assertAlmostEqual(source_support(shares, profile), expected)
        self.assertTrue(0 <= total_support(expected, 0.5) <= 1)
        with self.assertRaises(ValueError):
            source_support({**shares, "no_dataset": 0.0}, profile)

    def test_context_and_component_are_finite_bounded(self):
        self.assertTrue(0 <= population_context(0, 0) <= 1)
        self.assertTrue(0 <= population_context(100_000, 1_000_000) <= 1)
        self.assertAlmostEqual(component_gap(1.0, 0.05, True), 0.01)
        self.assertAlmostEqual(component_gap(0.0, 0.05, True), 0.06)
        self.assertEqual(component_gap(0.0, 0.05, False), 0.0)
        for profile in PROFILES.values():
            for beta in BETAS:
                for support in np.linspace(0, 1, 101):
                    value = component_gap(float(support), beta, True)
                    self.assertTrue(math.isfinite(value) and 0 <= value <= 0.1)

    def test_generated_candidate_hash_scope_and_output_bounds(self):
        report_path = REPO / "reports/building-source-prior.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        candidate_path = REPO / report["candidate_csv"]
        self.assertEqual(report["candidate_rows"], 9379)
        self.assertEqual(hashlib.sha256(candidate_path.read_bytes()).hexdigest(), report["candidate_csv_sha256"])
        self.assertEqual(set(report["per_region"]), set(REGIONS))
        self.assertFalse(report["restrictions"]["sample_score_placeholders_or_target_labels_read"])
        self.assertFalse(report["restrictions"]["reference_or_external_footprint_counts_or_geometry_read"])
        self.assertFalse(report["restrictions"]["leaderboard_or_score_feedback_used"])
        self.assertFalse(report["restrictions"]["upload_or_submission_made"])
        primary = report["global_sensitivity_by_profile_and_beta"]["primary"]["0.02"]
        self.assertEqual(primary["changed_vs_constant_baseline_tracts"], 9377)
        self.assertTrue(primary["within_preregistered_absolute_component_bound"])
        with candidate_path.open("r", newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 9379)
        self.assertEqual(len({row["GEOID"] for row in rows}), 9379)
        for row in rows:
            for profile in PROFILES:
                for beta in BETAS:
                    value = float(row[f"building_gap_{profile}_beta_{beta:.2f}"])
                    self.assertTrue(math.isfinite(value) and 0 <= value <= 0.1)


if __name__ == "__main__":
    unittest.main()
