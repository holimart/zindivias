from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('build_track_a', ROOT / 'scripts/build_track_a.py')
track_a = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(track_a)


class DawidSkeneTests(unittest.TestCase):
    def test_dawid_skene_is_deterministic_bounded_and_handles_missing_votes(self) -> None:
        observations = np.array([
            [1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0], [1.0, 1.0, np.nan], [0.0, np.nan, 0.0],
        ])
        first, details = track_a.dawid_skene(observations)
        second, details2 = track_a.dawid_skene(observations)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(details, details2)
        self.assertTrue(np.isfinite(first).all())
        self.assertTrue(((first >= 0) & (first <= 1)).all())
        self.assertGreater(first[0], first[2])

    def test_dawid_skene_rejects_single_witness_and_nonbinary_values(self) -> None:
        with self.assertRaises(ValueError):
            track_a.dawid_skene(np.array([[1.0], [0.0]]))
        with self.assertRaises(ValueError):
            track_a.dawid_skene(np.array([[0.3, 1.0], [0.0, 1.0]]))


class TrackARegistryTests(unittest.TestCase):
    def test_forbidden_ideas_are_never_selected(self) -> None:
        registry = {'ideas': [
            {'id': 'G1', 'status': 'implemented', 'component': 'R'},
            {'id': 'Tm1', 'status': 'implemented', 'component': 'B'},
            {'id': 'X2', 'status': 'implemented', 'component': 'B'},
        ]}
        emitted = {
            'G1': {'001': {'R:gap': {'channel': 'gap', 'component': 'R'}}},
            'Tm1': {'001': {'B:gap': {'channel': 'gap', 'component': 'B'}}},
            'X2': {'001': {'B:gap': {'channel': 'gap', 'component': 'B'}}},
        }
        self.assertEqual(track_a.eligible_gap_ideas(registry, emitted),
                         {'R': ['G1'], 'B': [], 'F': [], 'Est': []})


if __name__ == '__main__':
    unittest.main()
