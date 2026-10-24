import csv
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import shapely

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_facility_catchment_candidate import (  # noqa: E402
    ALPHA,
    CONFIG,
    baseline_type_gap,
    radius_features,
)


class FacilityCatchmentCandidateTests(unittest.TestCase):
    def test_preregistered_fixed_uplift_is_bounded(self):
        self.assertEqual(ALPHA, 0.05)
        self.assertEqual(CONFIG["catchments"]["radii_meters"], [1000, 5000, 10000])
        stratum = {"ALAND": 1_000_000, "pop_total": 1000,
                   "pop_urban": 500, "pct_urban": 0.5}
        feature = {"overture_poi_fire": 0, "overture_poi_unclassified": 0,
                   "overture_unclassified_fire_share": 0.25}
        base = baseline_type_gap("fire", stratum, feature)
        self.assertTrue(0 < base["p_ref_present"] < 1)
        p_adj = min(1.0, base["p_ref_present"] * (1.0 + ALPHA))
        delta = (p_adj - base["p_ref_present"]) * base["conditional_gap"]
        self.assertTrue(0 <= delta <= 0.05)

    def test_radius_counts_kernel_and_no_facility_fallback(self):
        targets = shapely.points([0.0, 3000.0], [0.0, 0.0])
        facilities = shapely.points([500.0], [0.0])
        count, distance, kernel, missing = radius_features(targets, facilities, 1000)
        self.assertEqual(count.tolist(), [1, 0])
        self.assertTrue(np.allclose(distance, [500.0, 2500.0]))
        self.assertTrue(np.allclose(kernel, np.exp(-distance / 1000)))
        self.assertEqual(missing.tolist(), [False, False])

        count, distance, kernel, missing = radius_features(
            targets, np.asarray([], dtype=object), 1000)
        self.assertEqual(count.tolist(), [0, 0])
        self.assertEqual(distance.tolist(), [0.0, 0.0])
        self.assertEqual(kernel.tolist(), [0.0, 0.0])
        self.assertEqual(missing.tolist(), [True, True])

    def test_generated_candidate_contract_hashes_bounds_and_radii(self):
        report_path = REPO / "reports/facility-catchment-candidate.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        candidate_path = REPO / report["candidate_csv"]
        manifest_path = REPO / report["candidate_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(report["candidate_id"], CONFIG["candidate_id"])
        self.assertEqual(report["candidate_rows"], 9379)
        self.assertEqual(manifest["candidate_rows"], 9379)
        self.assertEqual(hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
                         report["candidate_csv_sha256"])
        self.assertEqual(hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                         report["candidate_manifest_sha256"])
        self.assertTrue(report["measure_after"]["exact_id_and_output_checks"]["exact_official_order"])
        for radius in ("1km", "5km", "10km"):
            self.assertTrue(report["measure_after"]["exact_id_and_output_checks"][
                "max_delta_at_most_0_05_by_radius"][radius])
            self.assertIn(radius, report["measure_after"]["global_official"]["by_radius"])
        self.assertFalse(report["restrictions"]["sample_score_placeholders_read_or_used"])
        self.assertFalse(report["restrictions"]["USGS_facility_lists_or_coordinates_used"])
        self.assertFalse(report["restrictions"]["upload_or_submission_made"])

        with candidate_path.open("r", newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        candidate_ids = [row["GEOID"] for row in rows]
        self.assertEqual(len(candidate_ids), 9379)
        self.assertEqual(len(set(candidate_ids)), 9379)
        counts_by_region = {region: sum(row["region"] == region for row in rows)
                            for region in CONFIG["scope"]["regions"]}
        self.assertEqual(counts_by_region, {"maricopa-az": 1593, "northern-ca": 591,
                                            "eastern-ok": 1192, "south-central-tx": 6003})
        numeric = [column for column in rows[0]
                   if column not in {"GEOID", "region", "urbanity_cell", "ur_class", "urbanity_source"}]
        for row in rows:
            for column in numeric:
                self.assertTrue(math.isfinite(float(row[column])), (row["GEOID"], column))
            for column in ("facility_logistic_baseline", "facility_catchment_gap_1km",
                           "facility_catchment_gap_5km", "facility_catchment_gap_10km"):
                self.assertTrue(0 <= float(row[column]) <= 1)
            for column in ("facility_delta_1km", "facility_delta_5km", "facility_delta_10km"):
                self.assertTrue(-1e-12 <= float(row[column]) <= 0.05 + 1e-12)


if __name__ == "__main__":
    unittest.main()
