import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_s2_missingness_semantics.py"
SPEC = importlib.util.spec_from_file_location("s2_missingness_semantics", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class S2MissingnessSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.c2 = {"transport_gap": "0.2", "building_gap": "0.4", "poi_gap": "0.6"}
        self.estimates = {
            "transport_defined": "1", "building_defined": "1", "poi_defined": "1",
            "fire_defined": "1", "fire_gap": "0.3",
            "ems_defined": "1", "ems_gap": "0.7",
            "schools_defined": "1", "schools_gap": "0.5",
            "cbp_defined": "1", "cbp_gap": "0.8",
        }

    def test_missing_numeric_state_is_separate_from_numeric_zero(self):
        self.assertTrue(MODULE.missing_numeric(None))
        self.assertTrue(MODULE.missing_numeric(float("nan")))
        self.assertFalse(MODULE.missing_numeric(0))
        self.assertFalse(MODULE.missing_numeric(2.0))

    def test_zero_filled_and_explicit_indicator_keep_state_but_s2_drops_both(self):
        feature = {
            "overture_building_microsoft_source_share": None,
            "overture_poi_fire": None,
            "overture_poi_ems": 0,
            "overture_poi_schools": 2,
        }
        zero_score, zero_mask = MODULE.compose_variant(self.c2, self.estimates, feature, "zero_filled")
        explicit_score, explicit_mask = MODULE.compose_variant(
            self.c2, self.estimates, feature, "explicit_missing_indicator")
        self.assertEqual(zero_mask, {"fire": True, "ems": False, "schools": False})
        self.assertEqual(explicit_mask, zero_mask)
        self.assertEqual(zero_score, explicit_score)
        with self.assertRaises(ValueError):
            MODULE.compose_variant(self.c2, self.estimates, feature, "original")

    def test_spearman_handles_ties_and_perfect_order(self):
        self.assertAlmostEqual(MODULE.spearman([1, 1, 2, 3], [4, 4, 5, 6]), 1.0)
        self.assertAlmostEqual(MODULE.spearman([1, 2, 3], [3, 2, 1]), -1.0)

    def test_constant_rank_vector_is_reported_undefined(self):
        self.assertIsNone(MODULE.spearman([1, 1], [3, 4]))


if __name__ == "__main__":
    unittest.main()
