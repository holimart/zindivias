from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_population_weighted_feature import pearson_pair  # noqa: E402
from build_mx4p_feature import FEATURE_CHANNEL, output_table, parquet_bytes  # noqa: E402


class Mx4PopulationWeightedFeatureTests(unittest.TestCase):
    def test_output_has_separate_mx4p_f_covariate_and_preserves_defined_mask(self) -> None:
        table = output_table(["00000000001", "00000000002"], ["r", "r"], [0.25, 0.0], [True, False])
        self.assertEqual(table["idea"].to_pylist(), ["Mx4p", "Mx4p"])
        self.assertEqual(table["family"].to_pylist(), ["Mx", "Mx"])
        self.assertEqual(table["component"].to_pylist(), ["F", "F"])
        self.assertEqual(table["channel"].to_pylist(), [FEATURE_CHANNEL, FEATURE_CHANNEL])
        self.assertEqual(table["GEOID"].to_pylist()[0], "00000000001")
        self.assertEqual(table["defined"].to_pylist(), [True, False])
        self.assertEqual(parquet_bytes(table), parquet_bytes(table))

    def test_output_rejects_invalid_bounds_and_nonzero_undefined_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            output_table(["a"], ["r"], [1.01], [True])
        with self.assertRaises(ValueError):
            output_table(["a"], ["r"], [0.2], [False])

    def test_pairwise_pearson_masks_undefined_and_reports_degenerate_overlap(self) -> None:
        r, overlap = pearson_pair(np.array([0.0, 1.0, 2.0, np.nan]),
                                  np.array([0.0, 2.0, 4.0, 8.0]))
        self.assertEqual(overlap, 3)
        self.assertAlmostEqual(r, 1.0)
        constant_r, constant_overlap = pearson_pair(np.ones(4), np.arange(4.0))
        self.assertIsNone(constant_r)
        self.assertEqual(constant_overlap, 4)


if __name__ == "__main__":
    unittest.main()
