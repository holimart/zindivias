import csv
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_poi_confidence_candidate import (  # noqa: E402
    CONFIG,
    FAMILIES,
    THRESHOLDS,
    confidence_weighted_increment,
)


class PoiConfidenceCandidateTests(unittest.TestCase):
    def test_fixed_threshold_weighting_and_missing_confidence(self):
        self.assertEqual(confidence_weighted_increment(0.5, 0.5), 0.5)
        self.assertEqual(confidence_weighted_increment(0.69, 0.7), 0.0)
        self.assertEqual(confidence_weighted_increment(0.9, 0.9), 0.9)
        self.assertEqual(confidence_weighted_increment(None, 0.5), 0.0)
        self.assertEqual(confidence_weighted_increment(float("nan"), 0.5), 0.0)
        self.assertEqual(confidence_weighted_increment(1.01, 0.5), 0.0)
        self.assertEqual(THRESHOLDS, (0.5, 0.7, 0.9))
        self.assertEqual(FAMILIES, ("fire", "ems", "schools"))

    def test_generated_candidate_schema_ids_bounds_hashes_and_disclosure(self):
        report_path = REPO / "reports/poi-confidence-candidate.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        after = report["measure_after"]
        candidate_path = REPO / after["candidate_csv"]
        manifest_path = REPO / after["candidate_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(report["candidate_id"], CONFIG["candidate_id"])
        self.assertEqual(report["candidate_rows"], 9379)
        self.assertEqual(hashlib.sha256(candidate_path.read_bytes()).hexdigest(), report["candidate_csv_sha256"])
        self.assertEqual(hashlib.sha256(manifest_path.read_bytes()).hexdigest(), after["candidate_manifest_sha256"])
        self.assertEqual(manifest["candidate_csv_sha256"], report["candidate_csv_sha256"])
        self.assertEqual(manifest["candidate_rows"], 9379)
        self.assertTrue(after["deterministic_rerun"]["verified"])
        self.assertTrue(after["exact_official_ids_and_order"])
        self.assertTrue(after["all_candidate_scores_finite_and_in_0_1"])
        self.assertTrue(after["no_predictive_accuracy_measurement_possible"])
        self.assertFalse(after["target_rmse_computed"])
        self.assertEqual(set(after["threshold_candidates"]), set(CONFIG["scope"]["candidate_ids"]))

        with candidate_path.open("r", newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
        self.assertEqual(len(rows), 9379)
        self.assertEqual(len({row["GEOID"] for row in rows}), 9379)
        self.assertEqual(len({row["region"] for row in rows}), 4)
        for threshold in THRESHOLDS:
            suffix = f"t{int(threshold * 100):03d}"
            score_column = f"poi_gap_{suffix}"
            defined_column = f"poi_defined_{suffix}"
            self.assertTrue(all(math.isfinite(float(row[score_column])) and
                                0 <= float(row[score_column]) <= 1 for row in rows))
            self.assertTrue(all(int(row[defined_column]) in (0, 1) for row in rows))
            expected_id = CONFIG["scope"]["candidate_ids"][THRESHOLDS.index(threshold)]
            self.assertTrue(all(row[f"candidate_id_{suffix}"] == expected_id for row in rows))
        self.assertFalse(report["restrictions"]["sample_score_placeholders_read"])
        self.assertFalse(report["restrictions"]["labels_or_target_reconstruction_used"])
        self.assertFalse(report["restrictions"]["removed_reference_layers_or_equivalents_used"])
        self.assertFalse(report["restrictions"]["external_facility_lists_or_usgs_used"])
        self.assertFalse(report["restrictions"]["leaderboard_information_or_probes_used"])
        self.assertFalse(report["restrictions"]["upload_or_submission_made"])
        self.assertFalse(report["restrictions"]["score_fitted_threshold_or_weight"])
        self.assertFalse(report["restrictions"]["sources_used_as_truth_or_candidate_weights"])


if __name__ == "__main__":
    unittest.main()
