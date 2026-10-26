"""Tests for cross-theme witness idea X1 and the X-family governance screen."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.ideas.x import _facility_type, soft_facility_definedness  # noqa: E402


class X1ProbabilityTests(unittest.TestCase):
    def test_soft_probability_is_normalized_and_monotone(self) -> None:
        counts = np.array([[0., 0., 0.], [1., 0., 0.], [1., 2., 3.]])
        probability = soft_facility_definedness(counts)
        self.assertEqual(probability[0], 0.)
        self.assertTrue(np.all((probability >= 0.) & (probability <= 1.)))
        self.assertLess(probability[1], probability[2])
        self.assertAlmostEqual(probability[1], 1. - np.exp(-1.))

    def test_probability_rejects_invalid_counts(self) -> None:
        with self.assertRaises(ValueError): soft_facility_definedness(np.array([[1., -1., 0.]]))
        with self.assertRaises(ValueError): soft_facility_definedness(np.ones((2, 2)))
        with self.assertRaises(ValueError): soft_facility_definedness(np.array([[np.nan, 0., 0.]]))

    def test_facility_name_witness_guards_false_positives(self) -> None:
        seed = {"building_witnesses": {
            "schools": {"classes": ["school"], "name_patterns": [r"\bschool\b"],
                        "false_positive_patterns": [r"school of rock", r"driving school"]},
            "fire": {"classes": ["fire_station"], "name_patterns": [r"\bfire station\b"],
                     "false_positive_patterns": [r"firehouse subs"]},
            "ems": {"classes": ["ems_station"], "name_patterns": [r"\bems station\b"],
                    "false_positive_patterns": [r"emergency room"]}}}
        self.assertEqual(_facility_type("", {"primary": "Lincoln Elementary School"}, seed), {"schools"})
        self.assertEqual(_facility_type("", {"primary": "School of Rock"}, seed), set())
        self.assertEqual(_facility_type("fire_station", None, seed), {"fire"})


if __name__ == "__main__":
    unittest.main()
