"""G-family geometry estimator formulas, safety rules, and measured report contract."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bias60.ideas import IMPLEMENTATIONS  # noqa: E402
from bias60.ideas import g as geometry  # noqa: E402


class GFormulaTests(unittest.TestCase):
    def test_g1_boundary_share_and_frozen_side_prior(self) -> None:
        class Inputs:
            idea_id = None
            region = "r"
            geoids = ["a", "b"]
        with patch.object(geometry, "_road_length_summary",
                          return_value=(np.array([100., 0.]), np.array([20., 0.]), None,
                                        {"rows_scanned": 10})):
            result = geometry.g1(Inputs())
        self.assertEqual(result.emissions[0].channel, "gap")
        self.assertAlmostEqual(result.emissions[0].values[0], .1)
        self.assertAlmostEqual(result.emissions[1].values[0], .2)
        np.testing.assert_array_equal(result.emissions[0].defined, [True, False])

    @staticmethod
    def _road(name: str, route: str, coords: list[tuple[float, float]], direction: str,
              subclass: str | None = None) -> dict:
        return {"names": {"primary": name}, "routes": [{"network": "US:I", "ref": route}],
                "metric_geometry": shapely.LineString(coords), "travel_heading": direction,
                "subclass": subclass}

    def test_g2_pairs_named_opposite_oneways_under_60m(self) -> None:
        rows = [self._road("I-80", "80", [(0, 0), (100, 0)], "forward"),
                self._road("I-80", "80", [(0, 45), (100, 45)], "backward")]
        self.assertEqual(geometry.pair_dual_carriageways(rows), [(0, 1)])

    def test_g2_rejects_unmatched_route_and_ramp_frontage_fixtures(self) -> None:
        main = self._road("I-35", "35", [(0, 0), (100, 0)], "forward")
        unrelated = self._road("State Road", "99", [(0, 40), (100, 40)], "backward")
        ramp = self._road("I-35 Frontage", "35", [(0, 50), (100, 50)], "backward", "motorway_link")
        self.assertEqual(geometry.pair_dual_carriageways([main, unrelated]), [])
        self.assertEqual(geometry.pair_dual_carriageways([main, ramp]), [])

    def test_g2_rejects_same_travel_direction_even_when_geometry_is_parallel(self) -> None:
        rows = [self._road("US 1", "1", [(0, 0), (100, 0)], "forward"),
                self._road("US 1", "1", [(100, 40), (0, 40)], "backward")]
        self.assertEqual(geometry.pair_dual_carriageways(rows), [])

    def test_g3_redefines_denominator_from_leave_one_out_peer_tract_medians(self) -> None:
        class Inputs:
            region = "r"
            def strata(self, columns):
                return {"STATEFP": np.array(["01", "01", "01", "01"], object),
                        "COUNTYFP": np.array(["001", "001", "001", "003"], object),
                        "ruca_primary": np.array([1, 1, 1, 1], object)}
        refs, diagnostic = geometry._peer_area_references(
            Inputs(), [[2., 2.], [4.], [10.], [20.]])
        np.testing.assert_allclose(refs, [7., 6., 3., 4.])
        self.assertTrue(diagnostic["frozen_before_deficit"])
        self.assertIn("leave-one-tract-out", diagnostic["peer_reference"])

    def test_microsoft_buildings_never_enter_geometry_estimators(self) -> None:
        self.assertFalse(geometry._allowed_building([{"dataset": "Microsoft ML Buildings"}]))
        self.assertFalse(geometry._allowed_building([{"dataset": "OpenStreetMap"},
                                                     {"dataset": "Microsoft ML Buildings"}]))
        self.assertTrue(geometry._allowed_building([{"dataset": "OpenStreetMap"}]))
        self.assertTrue(geometry._allowed_building([{"dataset": "Esri"}]))

    def test_g4_ring_counts_only_eight_neighbors_and_honors_park_mask(self) -> None:
        occupied = {(x, y): 1 for x in range(3) for y in range(3) if (x, y) != (1, 1)}
        tract = shapely.box(-1000, -1000, 1000, 1000)
        should, voids, expected = geometry._ring_voids(occupied, tract, set(), [])
        self.assertGreaterEqual(should, 1)
        self.assertGreaterEqual(voids, 1)
        self.assertGreater(expected, 0)
        _, masked_voids, _ = geometry._ring_voids(occupied, tract, {(1, 1)}, [])
        self.assertEqual(masked_voids, voids - 1)

    def test_g5_exact_coordinate_stack_requires_five_distinct_names(self) -> None:
        stacks = {(1.0, 2.0): [(0, f"name-{i}") for i in range(5)],
                  (3.0, 4.0): [(0, f"same") for _ in range(10)]}
        qualifying = [members for members in stacks.values()
                      if len({name for _, name in members if name}) >= 5]
        self.assertEqual(len(qualifying), 1)
        self.assertEqual(len(qualifying[0]), 5)


class GRegistryAndReportTests(unittest.TestCase):
    def test_all_five_geometry_ideas_registered(self) -> None:
        for idea in (f"G{i}" for i in range(1, 6)):
            self.assertIn(idea, IMPLEMENTATIONS)

    def test_g_family_report_covers_outputs_hashes_and_inputs(self) -> None:
        report = json.loads((ROOT / "reports/g-family-screen.json").read_text())
        self.assertEqual(report["status"], "computed")
        self.assertEqual(set(report["scope"]["computed"]), {f"G{i}" for i in range(1, 6)})
        for idea, item in report["ideas"].items():
            with self.subTest(idea=idea):
                self.assertEqual(item["rows"], 9379 * len(item["emissions"]))
                self.assertEqual(item["official_rows"], 9379)
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(item["geoid_partition_exact"])
        self.assertFalse(report["constraints"]["microsoft_building_geometry_used"])
        self.assertFalse(report["constraints"]["labels_read"])
        self.assertEqual(report["constraints"]["uploads"], 0)


if __name__ == "__main__":
    unittest.main()
