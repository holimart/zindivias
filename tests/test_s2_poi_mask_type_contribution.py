import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_s2_poi_mask_type_contribution.py"
SPEC = importlib.util.spec_from_file_location("s2_poi_mask_type_contribution", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class S2PoiMaskTypeContributionTests(unittest.TestCase):
    def test_shapley_additive_game_matches_type_deltas(self):
        weights = {"fire": 0.03, "ems": -0.02, "schools": 0.04}
        values = {}
        for bits in range(8):
            mask = frozenset(kind for i, kind in enumerate(MODULE.FACILITIES) if bits & (1 << i))
            values[mask] = 0.25 + sum(weights[kind] for kind in mask)
        got = MODULE.shapley_attribution(values)
        for kind in weights:
            self.assertAlmostEqual(got[kind], weights[kind], places=14)
        self.assertAlmostEqual(sum(got.values()), values[frozenset(MODULE.FACILITIES)] - values[frozenset()], places=14)

    def test_shapley_recomposes_nonlinear_interactions(self):
        def value(mask):
            total = 0.2 + 0.01 * len(mask)
            if {"fire", "ems"}.issubset(mask):
                total += 0.07
            if {"ems", "schools"}.issubset(mask):
                total -= 0.03
            if set(MODULE.FACILITIES).issubset(mask):
                total += 0.02
            return total
        table = {}
        for bits in range(8):
            mask = frozenset(kind for i, kind in enumerate(MODULE.FACILITIES) if bits & (1 << i))
            table[mask] = value(mask)
        parts = MODULE.shapley_attribution(table)
        self.assertAlmostEqual(sum(parts.values()), value(frozenset(MODULE.FACILITIES)) - value(frozenset()), places=14)

    def test_mask_scenarios_only_restore_the_named_zero_count_type(self):
        base = {"transport_gap": 0.2, "building_gap": 0.4}
        estimates = {
            "transport_defined": 1, "building_defined": 1,
            "fire_defined": 1, "fire_gap": 0.1,
            "ems_defined": 1, "ems_gap": 0.6,
            "schools_defined": 1, "schools_gap": 0.9,
            "cbp_defined": 1, "cbp_gap": 0.3,
        }
        counts = {"fire": 0, "ems": 0, "schools": 2}
        frozen = MODULE.score_for_mask(base, estimates, counts, frozenset())
        fire_only = MODULE.score_for_mask(base, estimates, counts, frozenset({"fire"}))
        combined = MODULE.score_for_mask(base, estimates, counts, frozenset(MODULE.FACILITIES))
        self.assertNotEqual(frozen, fire_only)
        self.assertEqual(MODULE.score_for_mask(base, estimates, counts, frozenset()), frozen)
        self.assertAlmostEqual(combined, (0.2 + 0.4 + ((0.1 + 0.6 + 0.9) / 3 + 0.3) / 2) / 3)

    def test_strict_share_threshold_and_summary(self):
        summary = MODULE.summarize_contribution([0.05, -0.06, 0.0, 0.02])
        self.assertEqual(summary["tract_share_absolute_delta_gt_0_05"], 0.25)
        self.assertEqual(summary["n"], 4)


if __name__ == "__main__":
    unittest.main()
