import csv
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_candidate_stability_envelope import (  # noqa: E402
    CANDIDATE_ORDER,
    CONFIG,
    composite,
)


class CandidateStabilityEnvelopeTests(unittest.TestCase):
    def test_fixed_candidate_set_and_defined_component_aggregation(self):
        self.assertEqual(len(CANDIDATE_ORDER), 6)
        self.assertEqual(CONFIG["composition"]["envelope_aggregation"],
                         "equal-weight arithmetic mean of the six frozen full-score candidates; no weights are estimated or adjusted")
        score, defined = composite([(0.2, 1), (0.8, 1), (0.99, 0)])
        self.assertEqual(score, 0.5)
        self.assertEqual(defined, 1)
        score, defined = composite([(0.2, 0), (0.8, 0)])
        self.assertEqual((score, defined), (0.0, 0))

    def test_generated_envelope_ids_hashes_spread_and_bounds(self):
        report_path = REPO / "reports/candidate-stability-envelope.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        after = report["measure_after"]
        manifest_path = REPO / after["manifest"]
        members_path = REPO / after["member_csv"]
        envelope_path = REPO / after["envelope_csv"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(report["candidate_id"], CONFIG["candidate_id"])
        self.assertEqual(after["official_rows"], 9379)
        self.assertEqual(after["candidate_order"], list(CANDIDATE_ORDER))
        self.assertTrue(after["deterministic_rerun"]["verified"])
        self.assertTrue(after["operator_review_required"])
        self.assertGreater(after["candidate_spread"]["q95"], 0.10)
        self.assertEqual(hashlib.sha256(members_path.read_bytes()).hexdigest(), after["member_csv_sha256"])
        self.assertEqual(hashlib.sha256(envelope_path.read_bytes()).hexdigest(), after["envelope_csv_sha256"])
        self.assertEqual(hashlib.sha256(manifest_path.read_bytes()).hexdigest(), after["manifest_sha256"])
        self.assertEqual(manifest["member_csv_sha256"], after["member_csv_sha256"])
        self.assertEqual(manifest["envelope_csv_sha256"], after["envelope_csv_sha256"])

        with envelope_path.open("r", newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 9379)
        ids = [row["GEOID"] for row in rows]
        self.assertEqual(len(set(ids)), 9379)
        regions = {region: sum(row["region"] == region for row in rows)
                   for region in CONFIG["scope"]["regions_order"]}
        self.assertEqual(regions, {"maricopa-az": 1593, "northern-ca": 591,
                                   "eastern-ok": 1192, "south-central-tx": 6003})
        for row in rows:
            candidate_values = [float(row[name]) for name in CANDIDATE_ORDER]
            self.assertTrue(all(math.isfinite(value) and 0 <= value <= 1 for value in candidate_values))
            self.assertAlmostEqual(float(row["spread"]), max(candidate_values) - min(candidate_values), places=10)
            self.assertAlmostEqual(float(row["equal_weight_mean"]), sum(candidate_values) / len(candidate_values), places=10)
            self.assertEqual(int(row["spread_gt_0_05"]), int(float(row["spread"]) > 0.05))
        self.assertTrue(after["schema_bounds_alignment_determinism_checks"]["all_candidate_rows_aligned"])
        self.assertFalse(report["restrictions"]["sample_score_placeholders_read"])
        self.assertFalse(report["restrictions"]["labels_or_target_reconstruction_used"])
        self.assertFalse(report["restrictions"]["upload_or_submission_made"])
        self.assertFalse(report["restrictions"]["scored_candidate_promotion"])


if __name__ == "__main__":
    unittest.main()
