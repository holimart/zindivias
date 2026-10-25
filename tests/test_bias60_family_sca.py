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


if __name__ == "__main__":
    unittest.main()
