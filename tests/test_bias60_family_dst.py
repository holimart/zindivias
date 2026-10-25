"""Tests for distributional-shape idea formulas and documented safety rails."""
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
from bias60.ideas.dst3 import expected_left_tail  # noqa: E402
from bias60.ideas.dst import dst1, dst2  # noqa: E402
from bias60.ideas.dst_meta import _fit_diagonal_mixture, calibrate_probability_mean  # noqa: E402

CONFIG = json.loads((ROOT / "config/bias60-harness-preregistered.json").read_text())


class Dst3InputStub:
    config = CONFIG
    region = "eastern-ok"
    geoids = ["ok0000001", "ok0000002", "ok0000003"]

    def building_area_bins(self, small_m2: float, upper_m2: float):
        self.asserted_bins = (small_m2, upper_m2)
        return ({"below_small": np.array([0., 2., 0.]),
                 "in_band": np.array([5., 5., 0.])},
                {"in_band_features": 2000, "log_area_sum": 2000 * math.log(100.),
                 "log_area_squared_sum": 2000 * (math.log(100.) ** 2 + 0.5),
                 "layer": "buildings", "microsoft_reference_used": False})


class PlaceSummaryStub:
    def __init__(self, *, overlap: np.ndarray | None = None) -> None:
        self.config = CONFIG
        self.region = "eastern-ok"
        self.geoids = [f"ok{i:07d}" for i in range(4)]
        self._overlap = np.zeros(4) if overlap is None else overlap

    def poi_shape_capture(self, source_a: str, source_b: str):
        a = np.array([20., 1., 0., 0.])
        b = np.array([15., 1., 0., 0.])
        overlap = self._overlap
        union = a + b - overlap
        return ({"n_categories": np.array([10., 10., 0., 10.]), "s_obs": np.array([8., 8., 0., 10.]),
                 "f1": np.array([2., 0., 0., 10.]), "f2": np.array([1., 1., 0., 0.]),
                 "poi_total": np.array([26., 2., 0., 10.]),
                 "source_a": a, "source_b": b, "source_overlap": overlap,
                 "source_union": union}, {"layer": "pois", "microsoft_membership_used": False,
                                           "foursquare_membership_used": False})

    def strata(self, columns: list[str]):
        data = {"COUNTYFP": np.array(["001", "001", "002", "002"], dtype=object)}
        return {column: data[column] for column in columns}


class Dst1AndDst2Tests(unittest.TestCase):
    def test_dst1_good_turing_missing_mass_is_shrunk_toward_county(self) -> None:
        output = dst1(PlaceSummaryStub())
        output.validate(config=CONFIG)
        emission = output.emissions[0]
        np.testing.assert_array_equal(emission.defined, [True, True, False, True])
        np.testing.assert_allclose(emission.values[[0, 1, 3]], [19.0 / 252.0, 1.0 / 16.8, 1.0])
        self.assertEqual(output.diagnostics["chao1_defined"], 2)

    def test_dst2_marks_zero_overlap_as_measured_undefined_not_zero_gap(self) -> None:
        output = dst2(PlaceSummaryStub())
        output.validate(config=CONFIG)
        emission = output.emissions[0]
        self.assertFalse(emission.defined.any())
        np.testing.assert_array_equal(emission.values, np.zeros(4))

    def test_dst2_uses_tract_chapman_when_overlap_reaches_preregistered_floor(self) -> None:
        inputs = PlaceSummaryStub(overlap=np.array([10., 0., 0., 0.]))
        output = dst2(inputs)
        emission = output.emissions[0]
        self.assertTrue(bool(emission.defined[0]))
        self.assertEqual(output.diagnostics["source_a"], "meta")
        self.assertEqual(output.diagnostics["source_b"], "alltheplaces")


class DstMetaTests(unittest.TestCase):
    def test_mixture_orients_higher_gap_class_as_incomplete(self) -> None:
        complete = np.tile([-0.1, -0.2, -0.1], (40, 1))
        incomplete = np.tile([-2.0, -1.2, -2.3], (40, 1))
        posterior, diagnostic = _fit_diagonal_mixture(np.vstack([complete, incomplete]))
        self.assertGreater(posterior[60:].mean(), posterior[:20].mean())
        self.assertIn(diagnostic["incomplete_class"], (0, 1))

    def test_organizer_moment_calibration_is_monotone_and_matches_mean(self) -> None:
        base = np.linspace(0.01, 0.99, 101)
        calibrated, offset = calibrate_probability_mean(base, 0.37)
        self.assertTrue(np.isfinite(offset))
        self.assertTrue(np.all(np.diff(calibrated) >= 0.0))
        self.assertAlmostEqual(float(calibrated.mean()), 0.37, places=10)


class Dst3Tests(unittest.TestCase):
    def test_expected_small_count_uses_lognormal_probability_ratio(self) -> None:
        # For a lognormal centered at 100 m2, both probabilities are analytic by symmetry.
        result = expected_left_tail(np.array([10.0]), math.log(100.0), 1.0, 40., 400.)
        p_small = 0.5 * (1 + math.erf((math.log(40.) - math.log(100.)) / math.sqrt(2)))
        p_band = 0.5 * (1 + math.erf((math.log(400.) - math.log(100.)) / math.sqrt(2))) - p_small
        self.assertAlmostEqual(result[0], 10.0 * p_small / p_band, places=12)

    def test_invalid_fit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            expected_left_tail(np.array([1.]), math.log(100.), 0.0)

    def test_harness_output_has_defined_flags_and_uses_provided_buildings_only(self) -> None:
        stub = Dst3InputStub()
        output = IMPLEMENTATIONS["Dst3"](stub)
        output.validate(config=CONFIG)
        self.assertEqual(stub.asserted_bins, (40.0, 400.0))
        self.assertEqual(output.emissions[0].component, "B")
        np.testing.assert_array_equal(output.emissions[0].defined, [True, True, False])
        self.assertFalse(output.diagnostics["microsoft_reference_used"])


if __name__ == "__main__":
    unittest.main()
