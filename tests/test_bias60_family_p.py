"""Unit and feasibility tests for the physical/normative P family."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.ideas import IMPLEMENTATIONS  # noqa: E402

CONFIG = json.loads((ROOT / "config/bias60-harness-preregistered.json").read_text())


class StubInputs:
    def __init__(self, region: str, *, feature: dict[str, np.ndarray],
                 strata: dict[str, np.ndarray]) -> None:
        self.config = CONFIG
        self.region = region
        self.geoids = [f"{region[:2]}{i:06d}00" for i in range(len(next(iter(feature.values()))))]
        self._feature = feature
        self._strata = strata

    def features(self, columns: list[str]) -> dict[str, np.ndarray]:
        return {column: self._feature[column] for column in columns}

    def strata(self, columns: list[str]) -> dict[str, np.ndarray]:
        return {column: self._strata[column] for column in columns}


class PhysicalPriorTests(unittest.TestCase):
    def test_p2_uses_fixed_ruca_area_expectations_and_ratio_gap(self) -> None:
        inputs = StubInputs(
            "eastern-ok",
            feature={"overture_poi_fire": np.array([0.0, 2.0, 0.0, 5.0])},
            strata={"ALAND": np.array([10e6, 100e6, 250e6, 500e6]),
                    "pop_total": np.array([500.0, 500.0, 100.0, 1000.0]),
                    "ruca_primary": np.array([2.0, 5.0, 8.0, 10.0])})
        output = IMPLEMENTATIONS["P2"](inputs)
        output.validate(config=CONFIG)
        emission = output.emissions[0]
        np.testing.assert_allclose(emission.values, [1.0, 0.0, 1.0, 0.0])
        self.assertTrue(emission.defined.all())
        self.assertEqual(emission.component, "F")
        self.assertEqual(output.diagnostics["expected_fire_distribution"]["count"], 4)

    def test_p2_keeps_unknown_ruca_undefined_instead_of_filling(self) -> None:
        inputs = StubInputs(
            "eastern-ok", feature={"overture_poi_fire": np.array([0.0, np.nan])},
            strata={"ALAND": np.array([10e6, 20e6]), "pop_total": np.array([2.0, 3.0]),
                    "ruca_primary": np.array([1.0, 99.0])})
        emission = IMPLEMENTATIONS["P2"](inputs).emissions[0]
        np.testing.assert_array_equal(emission.defined, [True, False])
        np.testing.assert_array_equal(emission.values, [1.0, 0.0])

    def test_p4_calibrates_only_to_the_frozen_rural_top_tercile(self) -> None:
        density = np.array([1., 2., 3., 4., 5., 6., 10.])
        inputs = StubInputs(
            "eastern-ok", feature={"overture_named_road_km_5070": density},
            strata={"ALAND": np.full(7, 1e6),
                    "ruca_primary": np.array([7., 8., 9., 10., 7., 8., 2.])})
        output = IMPLEMENTATIONS["P4"](inputs)
        output.validate(config=CONFIG)
        emission = output.emissions[0]
        self.assertEqual(emission.defined.sum(), 6)
        self.assertAlmostEqual(output.diagnostics["rho_km_per_km2"], 5.5)
        self.assertAlmostEqual(emission.values[0], 1.0 - 1.0 / 5.5)
        self.assertFalse(bool(emission.defined[6]))
        self.assertEqual(emission.values[6], 0.0)

    def test_p4_is_undefined_outside_its_preregistered_regions(self) -> None:
        inputs = StubInputs(
            "maricopa-az", feature={"overture_named_road_km_5070": np.array([1., 2.])},
            strata={"ALAND": np.array([1e6, 1e6]), "ruca_primary": np.array([7., 8.])})
        emission = IMPLEMENTATIONS["P4"](inputs).emissions[0]
        self.assertFalse(emission.defined.any())
        np.testing.assert_array_equal(emission.values, [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
