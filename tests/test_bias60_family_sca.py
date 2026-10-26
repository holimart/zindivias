"""Tests for Sca1's exact conditional-Poisson clipped-ratio estimator."""
from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.ideas import IMPLEMENTATIONS  # noqa: E402
from bias60.ideas.sca import conditional_poisson_clipped_ratio  # noqa: E402
from bias60.ideas.sca_extra import (assignment_variants, expected_definedness_composite,
                                   first_ring_neighbors, mean_nearest_neighbor_index,
                                   multiscale_log_oe_slope)  # noqa: E402

CONFIG = json.loads((ROOT / "config/bias60-harness-preregistered.json").read_text())


class StubInputs:
    def __init__(self) -> None:
        self.config = CONFIG
        self.region = "eastern-ok"
        self.geoids = [f"ok{i:07d}" for i in range(4)]
        self._features = {"overture_poi_fire": np.array([0.0, 1.0, 2.0, 0.0])}
        self._strata = {
            "ALAND": np.array([10e6, 100e6, 250e6, 20e6]),
            "pop_total": np.array([10.0, 20.0, 30.0, 40.0]),
            "ruca_primary": np.array([1.0, 5.0, 8.0, 99.0]),
        }

    def features(self, columns: list[str]) -> dict[str, np.ndarray]:
        return {column: self._features[column] for column in columns}

    def strata(self, columns: list[str]) -> dict[str, np.ndarray]:
        return {column: self._strata[column] for column in columns}


class Sca1Tests(unittest.TestCase):
    def test_matches_direct_conditional_poisson_sum(self) -> None:
        for rate, observed in ((0.1, 0.0), (0.7, 1.0), (2.0, 1.0), (10.0, 3.0)):
            with self.subTest(rate=rate, observed=observed):
                denominator = -math.expm1(-rate)
                probability = math.exp(-rate)
                weighted = 0.0
                for count in range(1, 300):
                    probability *= rate / count
                    weighted += probability * max(0.0, 1.0 - observed / count)
                brute = weighted / denominator
                self.assertAlmostEqual(
                    conditional_poisson_clipped_ratio(rate, observed), brute, places=12)

    def test_zero_observed_is_one_and_rate_extremes_remain_bounded(self) -> None:
        for rate in (1e-4, 0.01, 1.0, 100.0, 690.0):
            with self.subTest(rate=rate):
                self.assertEqual(conditional_poisson_clipped_ratio(rate, 0.0), 1.0)
                value = conditional_poisson_clipped_ratio(rate, max(0.0, rate - 2.0))
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_invalid_poisson_inputs_are_rejected(self) -> None:
        for rate, observed in ((0.0, 0.0), (-1.0, 0.0), (1.0, -1.0), (float("nan"), 0.0)):
            with self.subTest(rate=rate, observed=observed):
                with self.assertRaises(ValueError):
                    conditional_poisson_clipped_ratio(rate, observed)

    def test_harness_output_uses_p2_rate_and_preserves_undefined(self) -> None:
        output = IMPLEMENTATIONS["Sca1"](StubInputs())
        output.validate(config=CONFIG)
        emission = output.emissions[0]
        self.assertEqual(emission.component, "F")
        np.testing.assert_array_equal(emission.defined, [True, True, True, False])
        self.assertEqual(emission.values[0], 1.0)
        self.assertEqual(emission.values[3], 0.0)
        self.assertEqual(output.diagnostics["defined"], 3)
        self.assertTrue(output.diagnostics["conditioned_on_positive_denominator"])


class Sca2Tests(unittest.TestCase):
    def test_first_ring_and_county_aggregations_conserve_exact_fixture_counts(self) -> None:
        from shapely.geometry import box
        polygons = np.asarray([box(0, 0, 1, 1), box(1, 0, 2, 1), box(2, 0, 3, 1)], dtype=object)
        neighbors = first_ring_neighbors(polygons)
        self.assertEqual(neighbors, [[1], [0, 2], [1]])
        slope, defined, meta = multiscale_log_oe_slope(
            np.array([2., 3., 1.]), np.array([4., 3., 3.]), np.array([1., 2., 7.]),
            ["01001", "01001", "01001"], neighbors)
        self.assertEqual(meta["tract_scale_totals"][0].tolist(),
                         [[2.0, 4.0, 1.0], [5.0, 7.0, 3.0], [6.0, 10.0, 10.0]])
        self.assertEqual(meta["count_conservation"]["tract_observed_sum"], 6.0)
        self.assertEqual(meta["count_conservation"]["county_observed_sum_once"], 6.0)
        self.assertEqual(meta["count_conservation"]["tract_expected_sum"], 10.0)
        self.assertEqual(meta["count_conservation"]["county_expected_sum_once"], 10.0)
        self.assertTrue(defined[0])
        self.assertTrue(np.isfinite(slope[0]))
        self.assertFalse(defined[1])  # its first-ring and county scales are identical

    def test_zero_observed_count_has_no_pseudocount(self) -> None:
        values, defined, _ = multiscale_log_oe_slope(
            np.array([0., 2., 3.]), np.ones(3), np.array([1., 2., 3.]),
            ["x", "x", "x"], [[1], [0, 2], [1]])
        self.assertFalse(defined[0])
        self.assertEqual(values[0], 0.0)


class Sca3Tests(unittest.TestCase):
    def test_centroid_intersection_and_largest_overlap_are_deterministic(self) -> None:
        from shapely.geometry import Point, box, LineString
        tracts = np.asarray([box(0, 0, 1, 1), box(1, 0, 2, 1)], dtype=object)
        geoids = ["02", "01"]  # tie breaks by GEOID, not tree order
        buildings = np.asarray([box(.1, .1, .3, .3), box(.9, .2, 1.1, .4)], dtype=object)
        assigned = assignment_variants(buildings, tracts, geoids, kind="count")
        self.assertEqual(assigned["centroid"].tolist(), [1.0, 0.0])
        self.assertEqual(assigned["intersects"].tolist(), [2.0, 1.0])
        self.assertEqual(assigned["largest_overlap"].tolist(), [1.0, 1.0])
        self.assertTrue(assigned["largest_conserves_intersecting_features"])
        points = np.asarray([Point(1.0, .5)], dtype=object)
        point_assignments = assignment_variants(points, tracts, geoids, kind="count")
        self.assertEqual(point_assignments["centroid"].sum(), 0.0)
        self.assertEqual(point_assignments["intersects"].sum(), 2.0)
        self.assertEqual(point_assignments["largest_overlap"].tolist(), [0.0, 1.0])
        roads = np.asarray([LineString([(.5, .5), (1.5, .5)])], dtype=object)
        line_assignments = assignment_variants(roads, tracts, geoids, kind="length")
        np.testing.assert_allclose(line_assignments["intersects"], [.5, .5])
        self.assertEqual(line_assignments["intersects"].sum(), 1.0)  # clipped-length conservation
        self.assertEqual(line_assignments["centroid"].sum(), 0.0)
        self.assertEqual(line_assignments["largest_overlap"].tolist(), [0.0, 1.0])
        self.assertEqual(line_assignments["largest_overlap"].sum(), 1.0)


class Sca4Tests(unittest.TestCase):
    def test_clark_evans_index_and_small_n(self) -> None:
        points = np.asarray([[0., 0.], [3., 4.]])
        self.assertAlmostEqual(mean_nearest_neighbor_index(points, 100.0),
                               2.0 * 5.0 * math.sqrt(2.0 / 100.0))
        self.assertIsNone(mean_nearest_neighbor_index(points[:1], 100.0))
        self.assertIsNone(mean_nearest_neighbor_index(np.empty((0, 2)), 100.0))
        self.assertEqual(mean_nearest_neighbor_index(np.asarray([[0., 0.], [0., 0.]]), 100.0), 0.0)


class Sca5Tests(unittest.TestCase):
    def test_all_eight_patterns_are_normalized_and_composite_is_conditional(self) -> None:
        gaps = np.asarray([[.2, .4, .6], [.2, .4, .6], [.2, .4, .6]])
        probabilities = np.asarray([[1., 1., 1.], [0., 0., 0.], [.5, 1., 0.]])
        values, defined, pattern_mass = expected_definedness_composite(gaps, probabilities)
        np.testing.assert_allclose(pattern_mass.sum(axis=1), 1.0)
        self.assertAlmostEqual(values[0], .4)
        self.assertTrue(defined[0])
        self.assertEqual(values[1], 0.0)
        self.assertFalse(defined[1])
        self.assertAlmostEqual(values[2], .35)
        self.assertTrue(defined[2])

    def test_probability_and_gap_edges_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            expected_definedness_composite(np.asarray([[.1, .2, .3]]), np.asarray([[0., 1.1, .2]]))
        with self.assertRaises(ValueError):
            expected_definedness_composite(np.asarray([[.1, .2, np.nan]]), np.asarray([[.1, .2, .3]]))


if __name__ == "__main__":
    unittest.main()
