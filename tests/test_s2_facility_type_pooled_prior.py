import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_s2_facility_type_pooled_prior.py"
SPEC = importlib.util.spec_from_file_location("s2_pooled_prior", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class PooledPriorTests(unittest.TestCase):
    def test_zero_strength_is_local_and_larger_strength_moves_toward_prior(self):
        self.assertEqual(MODULE.posterior_rate(0, 1, 0.4, 0), 0)
        self.assertAlmostEqual(MODULE.posterior_rate(0, 1, 0.4, 2), 0.8 / 3)
        self.assertAlmostEqual(MODULE.posterior_rate(0, 1, 0.4, 5), 2 / 6)

    def test_well_supported_zero_stays_closer_to_local_zero(self):
        low = MODULE.posterior_rate(0, 0.2, 0.4, 2)
        high = MODULE.posterior_rate(0, 4.0, 0.4, 2)
        self.assertGreater(low, high)

    def test_pooled_rate_is_support_weighted_and_bounded(self):
        self.assertEqual(MODULE.pooled_rate(4, 10), 0.4)
        self.assertEqual(MODULE.pooled_rate(14, 10), 1.0)
        with self.assertRaises(ValueError):
            MODULE.pooled_rate(1, 0)

    def test_kappa_zero_preserves_zero_type_drop_and_local_positive_type(self):
        estimates = {
            "fire_defined": "0", "fire_gap": "0",
            "ems_defined": "1", "ems_gap": "0.4",
            "schools_defined": "0", "schools_gap": "0",
            "cbp_defined": "0", "cbp_gap": "0",
        }
        features = {"overture_poi_fire": 0, "overture_poi_ems": 2, "overture_poi_schools": 0}
        expected = {"fire": 0.2, "ems": 1.0, "schools": 0.3}
        prior = {"fire": 0.4, "ems": 0.4, "schools": 0.4}
        value0, defined0 = MODULE.poi_score("tract", {}, estimates, features, expected, prior, 0)
        value2, defined2 = MODULE.poi_score("tract", {}, estimates, features, expected, prior, 2)
        self.assertEqual((value0, defined0), (0.4, 1))
        self.assertEqual(defined2, 1)
        self.assertNotEqual(value2, value0)

    def test_support_bins_are_fixed(self):
        self.assertEqual(MODULE.support_bin(0.499), "low")
        self.assertEqual(MODULE.support_bin(0.5), "medium")
        self.assertEqual(MODULE.support_bin(1.0), "high")


if __name__ == "__main__":
    unittest.main()
