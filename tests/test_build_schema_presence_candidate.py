import csv
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

import pyarrow as pa

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_schema_presence_candidate import (  # noqa: E402
    CONFIG,
    MAX_ADJUSTMENT,
    THEMES,
    _top_schema,
    compute_candidate,
    status_masks,
)


class SchemaPresenceCandidateTests(unittest.TestCase):
    def test_registry_accounts_for_each_top_level_schema_field(self):
        for theme in THEMES:
            entry = CONFIG["field_registry"][theme]
            self.assertEqual(set(entry["top_level_schema_fields"]),
                             set(entry["modeled_optional_fields"]) | set(entry["explicitly_excluded_fields"]))
            self.assertFalse(set(entry["modeled_optional_fields"]) & set(entry["explicitly_excluded_fields"]))
            self.assertEqual(_top_schema(theme), entry["top_level_schema_fields"])

    def test_status_masks_separate_null_empty_explicit_unknown_and_known(self):
        dtype = pa.struct([
            ("primary", pa.string()),
            ("common", pa.map_(pa.string(), pa.string())),
        ])
        values = pa.array([
            None,
            {"primary": "", "common": []},
            {"primary": " UNKNOWN ", "common": []},
            {"primary": "clinic", "common": []},
            {"primary": "unknown", "common": [("en", "Clinic")]},
        ], type=dtype)
        masks = status_masks(values, scan_unknown=True)
        self.assertEqual(masks["missing"].tolist(), [True, False, False, False, False])
        self.assertEqual(masks["present_empty"].tolist(), [False, True, False, False, False])
        self.assertEqual(masks["present_explicit_unknown"].tolist(), [False, False, True, False, False])
        self.assertEqual(masks["present_known"].tolist(), [False, False, False, True, False])
        self.assertEqual(masks["present_mixed_unknown_known"].tolist(), [False, False, False, False, True])
        # Explicit unknown is a nonempty/present value, not missingness.
        self.assertTrue(masks["present_explicit_unknown"][2])
        self.assertFalse(masks["missing"][2])

    def test_optional_field_drop_recomputes_support_without_exceeding_bound(self):
        ids = ["a", "b"]
        region_by_id = {"a": "maricopa-az", "b": "maricopa-az"}
        base = {"a": 0.4, "b": 0.4}
        theme_rows = {}
        for theme in THEMES:
            theme_rows[theme] = {
                "a": {"assigned_feature_count": 10, "field_presence_counts": {"x": 8, "y": 8}},
                "b": {"assigned_feature_count": 10, "field_presence_counts": {"x": 2, "y": 2}},
            }
        all_fields = {theme: ["x", "y"] for theme in THEMES}
        full, _ = compute_candidate(base, region_by_id, theme_rows, field_keep=all_fields)
        dropped, _ = compute_candidate(base, region_by_id, theme_rows,
                                       field_keep={theme: ["x"] for theme in THEMES})
        self.assertEqual(list(full), ids)
        self.assertTrue(all(abs(full[geoid]["candidate_score"] - dropped[geoid]["candidate_score"])
                            <= MAX_ADJUSTMENT + 1e-12 for geoid in ids))
        self.assertTrue(all(0 <= full[geoid]["candidate_score"] <= 1 for geoid in ids))

    def test_generated_candidate_contract_hashes_and_accuracy_caveat(self):
        report_path = REPO / "reports/schema-presence-candidate.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        candidate_path = REPO / report["candidate_csv"]
        manifest_path = REPO / report["candidate_manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        after = report["measure_after"]
        self.assertEqual(report["candidate_rows"], 9379)
        self.assertEqual(report["candidate_id"], CONFIG["candidate_id"])
        self.assertEqual(hashlib.sha256(candidate_path.read_bytes()).hexdigest(), report["candidate_csv_sha256"])
        self.assertEqual(hashlib.sha256(manifest_path.read_bytes()).hexdigest(), report["candidate_manifest_sha256"])
        self.assertEqual(manifest["candidate_csv_sha256"], report["candidate_csv_sha256"])
        self.assertTrue(report["candidate_output"]["exact_official_ids_and_order"])
        self.assertTrue(report["candidate_output"]["all_scores_finite_and_bounded"])
        self.assertFalse(report["candidate_output"]["target_rmse_computed"])
        self.assertFalse(report["candidate_output"]["accuracy_claim_supported"])
        self.assertLessEqual(after["maximum_absolute_candidate_adjustment"], MAX_ADJUSTMENT + 1e-12)
        perturb = after["optional_field_drop_perturbation_tests"]
        self.assertTrue(perturb["all_single_field_drops_within_0_05"])
        self.assertTrue(perturb["all_optional_fields_unavailable_neutral_fallback"]["candidate_equals_C2"])
        with candidate_path.open("r", newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 9379)
        self.assertEqual(len({row["GEOID"] for row in rows}), 9379)
        for row in rows:
            score = float(row["coverage_gap_score"])
            delta = float(row["candidate_adjustment"])
            self.assertTrue(math.isfinite(score) and 0 <= score <= 1)
            self.assertTrue(math.isfinite(delta) and abs(delta) <= MAX_ADJUSTMENT + 1e-12)
        ordered_ids = [row["GEOID"] for row in rows]
        ordered_hash = hashlib.sha256(json.dumps(ordered_ids, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.assertEqual(ordered_hash, report["candidate_output"]["ordered_official_geoid_sha256"])
        self.assertEqual(perturb["fields_tested"], 44)
        for region in CONFIG["scope"]["official_regions_order"]:
            self.assertEqual(set(report["field_coverage_matrix_by_region_and_theme"][region]), set(THEMES))
            for theme in THEMES:
                theme_report = report["input_metadata_by_region"][region]["themes"][theme]
                attributes = report["field_coverage_matrix_by_region_and_theme"][region][theme]
                expected_fields = set(CONFIG["field_registry"][theme]["modeled_optional_fields"])
                self.assertEqual(set(attributes), expected_fields)
                self.assertTrue(theme_report["top_level_schema_registry_match"])
                for status in attributes.values():
                    self.assertTrue(status["schema_available"])
                    self.assertGreaterEqual(status["presence_rate_official_tract_assigned"], 0)
                    self.assertLessEqual(status["presence_rate_official_tract_assigned"], 1)
                    counts = status["status_counts_official_tract_assigned_rows"]
                    self.assertEqual(sum(counts.values()), theme_report["assignment"]["assigned_to_official_tracts"])
        self.assertTrue(all(value is False for value in report["restrictions"].values()))


if __name__ == "__main__":
    unittest.main()
